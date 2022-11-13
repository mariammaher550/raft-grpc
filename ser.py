import sys

import grpc

import raft_pb2 as pb2
import raft_pb2_grpc as pb2_grpc
import time
from multiprocessing import Queue
from threading import Thread
from queue import Empty
import os
import random
import socket
from concurrent import futures

CONFIG_PATH = "config.conf"  # overwrite this with your own config file path
SERVERS = {}
TERM, VOTED, STATE = 0, False, "follower"
LEADER_ID = None
IN_ELECTIONS = False  # will be true if it was suspended
START_SUSPEND = None  # time suspend started
TIME_LIMIT = random.randrange(150, 301)
SERVER_ID = sys.argv[1]
VOTES_RECEIVED = {"id": [], "address": []}


def read_config(path):
    global SERVERS
    with open(path) as configFile:
        lines = configFile.readline()
        for line in lines:
            parts = line.split()
            SERVERS[parts[0]] = f"{parts[1]}:{parts[2]}"


def start_server():
    global TERM, STATE, SERVERS
    server = grpc.Server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler, server)
    server.add_insecure_port(SERVERS[SERVER_ID])
    server.start()
    try:
        server.wait_for_termination()

    except KeyboardInterrupt:
        print(f"Server {SERVER_ID} is shutting down")

def driver():
    read_config(CONFIG_PATH)
    start_server()


if __name__ == '__main__':
    driver()
