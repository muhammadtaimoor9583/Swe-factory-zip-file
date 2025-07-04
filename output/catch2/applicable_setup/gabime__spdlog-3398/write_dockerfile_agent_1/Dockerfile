# Base image specification. Defines the foundation OS and architecture for the container
FROM --platform=linux/x86_64 ubuntu:22.04

# Set environment variables for non-interactive apt operations and timezone
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

# System dependencies installation. Installs essential tools and libraries required for C++ development and runtime
RUN apt update && apt install -y \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set default work directory to /testbed/
WORKDIR /testbed/

# Target Project setup. Clones source code, checks out to the target version, and configures it
RUN git clone https://github.com/gabime/spdlog /testbed \
    && cd /testbed \
    && git reset --hard 7e022c430053f71c3db80bf0eb3432392932f7e3 \
    && git remote remove origin \
    && chmod -R 777 /testbed

# Build the project, including tests
RUN mkdir build \
    && cd build \
    && cmake -DSPDLOG_BUILD_TESTS=ON .. \
    && cmake --build . -j$(nproc)