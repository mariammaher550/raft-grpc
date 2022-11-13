import sys
import grpc
import raft_pb2_grpc as pb2_grpc
address = ''
if __name__ == '__main__':
    global address
    while(True):
        command = sys.argv[1]
        if command == "connect":
            address = sys.argv[2]
        elif command == "getleader":
            channel = grpc.insecure_channel(address)
            stub = pb2_grpc.RaftServiceStub(channel)
            response = stub.GetLeader(**{})
            print(f"Leader id: {response.leaderId}. Leader address: {response.leaderAddress}.")
        elif command == "suspend":
            period = sys.argv[2]
            channel = grpc.insecure_channel(address)
            stub = pb2_grpc.RaftServiceStub(channel)
            response = stub.Suspend(**{"period": period})
        elif command == "quit":
            print("The client ends")
            exit(0)