FROM debian:latest

# Load dependencies
RUN apt-get update && apt-get install -y python git cmake ninja-build cppcheck valgrind g++ clang sudo % for dep in dependencies % % dep % % end %