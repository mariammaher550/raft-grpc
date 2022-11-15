import sys
import grpc
import raft_pb2_grpc as pb2_grpc
import raft_pb2 as pb2

address = ""

# Takes commands from input and processes them until it gets command 'quit'.
if __name__ == '__main__':
    while(True):
        line = input()
        command = line.split()[0]

        if command == "connect":
            address = (f"{line.split()[1]}:{line.split()[2]}")

        elif command == "getleader":
            channel = grpc.insecure_channel(address)
            stub = pb2_grpc.RaftServiceStub(channel)
            request = pb2.GetLeaderMessage(**{})
            response = stub.GetLeader(request)
            print(f"{response.leaderId} {response.leaderAddress}")

        elif command == "suspend":
            period = int(line.split()[1])
            channel = grpc.insecure_channel(address)
            stub = pb2_grpc.RaftServiceStub(channel)
            request = pb2.SuspendMessage(**{"period": period})
            response = stub.Suspend(request)

        elif command == "quit":
            print("The client ends")
            exit(0)
