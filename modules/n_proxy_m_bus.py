"""Opens a proxy/function bus between 2 ports for a N-proxy-M PubSub pattern

  Additional info:
2 modes:
1. proxy (no data copying) - DEFAULT
2. function (modify pass through data)
Similar to a ROS topic (named bus)

  ZeroMQ:
Default address listening to pubs: 127.0.0.1:5570
Default address publishing to subs: 127.0.0.1:5571
Sub listen and Pub style: 4+ part envelope (including key)
Subscription Key: all (openface, dnn)
Message parts:
0. sub_key
1. frame
2. timestamp
3. data
4. (data2)

TODO: register somewhere for a bus overview"""

# Copyright (c) Stef van der Struijk.
# License: GNU Lesser General Public License


import sys
import argparse
from functools import partial
import zmq.asyncio
import traceback
import logging
import numpy as np
import json
# import asyncio

# own import; if statement for documentation
if __name__ == '__main__':
    sys.path.append("..")
    from facsvatarzeromq import FACSvatarZeroMQ
    from smooth_data import SmoothData
else:
    from modules.facsvatarzeromq import FACSvatarZeroMQ
    from .smooth_data import SmoothData


class FACSvatarMessages(FACSvatarZeroMQ):
    """Publishes FACS and Head movement data from .csv files generated by OpenFace"""

    # def __init__(self, **kwargs):
    #     super().__init__(**kwargs)

    # overwrite existing start function
    def start(self, async_func_list=None):
        """No functions given --> data pass through only; else apply function on data before forwarding

        N publishers to 1 sub; proxy 1 sub to 1 pub; publish to M subscribers
        """

        # make sure pub / sub is initialised
        if not self.pub_socket or not self.sub_socket:
            print("Both pub and sub needs to be initiliased and set to bind")
            print("Pub: {}".format(self.pub_socket))
            print("Sub: {}".format(self.sub_socket))
            sys.exit()

        # apply function to data to passing through data
        if async_func_list:
            import asyncio
            # capture ZeroMQ errors; ZeroMQ using asyncio doesn't print out errors
            try:
                asyncio.get_event_loop().run_until_complete(asyncio.wait(
                    [func() for func in async_func_list]
                ))
            except Exception as e:
                print("Error with async function")
                # print(e)
                logging.error(traceback.format_exc())
                print()

            finally:
                # TODO disconnect pub/sub
                pass

        # don't duplicate the message, just pass through
        else:
            print("Try: Proxy... CONNECT!")
            zmq.proxy(self.pub_socket, self.sub_socket)
            print("CONNECT successful!")

    async def pub_sub_function(self, apply_function):  # async
        """Subscribes to FACS data, smooths, publishes it"""

        # class with data smoothing functions
        self.smooth_data = SmoothData()
        # get the function we need to pass data to
        smooth_func = getattr(self.smooth_data, apply_function)

        # await messages
        print("Awaiting FACS data...")
        # # without try statement, no error output
        try:
            # keep listening to all published message on topic 'facs'
            while True:
                msg = await self.sub_socket.recv_multipart()
                print()
                print(msg)

                # # change multiplier value; TODO separate from subscriber
                # if msg[0].decode('utf-8').startswith("command"):
                #     # JSON to list
                #     au_multiplier_list = json.loads(msg[2].decode('utf-8'))
                #
                #     # list to numpy array
                #     au_multiplier_np = np.array(au_multiplier_list)
                #     print("New multiplier: {}".format(au_multiplier_np))
                #
                #     # set new multiplier
                #     self.smooth_data.multiplier = au_multiplier_np

                # else:
                # check not finished; timestamp is empty (b'')
                if msg[1]:
                    msg[2] = json.loads(msg[2].decode('utf-8'))

                    # only pass on messages with enough tracking confidence; always send when no confidence param
                    if 'confidence' not in msg[2] or msg[2]['confidence'] >= 0.8:
                        # # don't smooth output of DNN
                        if not msg[0].decode('utf-8').startswith('facsvatar'):  # not
                            # TODO different history per user (init class more than once?)

                            # check au dict in data
                            if "au_r" in msg[2]:
                                # sort dict; dicts keep insert order Python 3.6+
                                au_r_dict = msg[2]['au_r']
                                au_r_sorted = dict(sorted(au_r_dict.items(), key=lambda k: k[0]))

                                # smooth facial expressions; window_size: number of past data points; steep: weight newer data
                                msg[2]['au_r'] = smooth_func(au_r_sorted, queue_no=0, window_size=4, steep=.35)
                            # check head rotation dict in data
                            if "pose" in msg[2]:
                                # smooth head position
                                msg[2]['pose'] = smooth_func(msg[2]['pose'], queue_no=1, window_size=4, steep=.2)
                            # else:
                            #     print("Data from DNN, forwarding unchanged")

                        # send modified message
                        print(msg)
                        await self.pub_socket.send_multipart([msg[0],  # topic
                                                              msg[1],  # timestamp
                                                              # data in JSON format or empty byte
                                                              json.dumps(msg[2]).encode('utf-8')
                                                              ])

                # send message we're done
                else:
                    print("No more messages to pass; finished")
                    await self.pub_socket.send_multipart([msg[0], b'', b''])

        except:
            print("Error with sub")
            # print(e)
            logging.error(traceback.format_exc())
            print()

    # receive commands
    async def set_parameters(self):
        print("Router awaiting commands")

        while True:
            try:
                [id_dealer, topic, data] = await self.rout_socket.recv_multipart()
                print("Command received from '{}', with topic '{}' and msg '{}'".format(id_dealer, topic, data))

                # set multiplier parameters
                if topic.decode('utf-8').startswith("multiplier"):
                    await self.set_multiplier(data)

            except Exception as e:
                print("Error with router function")
                # print(e)
                logging.error(traceback.format_exc())
                print()

    # set new multiplier values
    async def set_multiplier(self, data):
        # JSON to list
        au_multiplier_list = json.loads(data.decode('utf-8'))

        # list to numpy array
        au_multiplier_np = np.array(au_multiplier_list)
        print("New multiplier: {}".format(au_multiplier_np))

        # set new multiplier
        self.smooth_data.multiplier = au_multiplier_np


if __name__ == '__main__':
    # command line arguments; sockets have to use bind for N-1-M setup
    parser = argparse.ArgumentParser()

    # subscriber
    parser.add_argument("--sub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) pubslishers pub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--sub_port", default="5570",
                        help="Port publishers pub to; Default: 5570")
    parser.add_argument("--sub_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    # publisher
    parser.add_argument("--pub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) subscribers sub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--pub_port", default="5571",
                        help="Port subscribers sub to; Default: 5571")
    parser.add_argument("--pub_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    # router
    parser.add_argument("--rout_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) router listens to; Default: 127.0.0.1 (local)")
    parser.add_argument("--rout_port", default="5580",
                        help="Port dealers message to; Default: 5580")
    parser.add_argument("--rout_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    args, leftovers = parser.parse_known_args()
    print("The following arguments are used: {}".format(args))
    print("The following arguments are ignored: {}\n".format(leftovers))

    # init FACSvatar message class
    facsvatar_messages = FACSvatarMessages(**vars(args))
    # start processing messages; get reference to function without executing
    facsvatar_messages.start([partial(facsvatar_messages.pub_sub_function, "trailing_moving_average"),
                              facsvatar_messages.set_parameters])
