FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        ca-certificates \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY . /work

RUN cmake -S . -B build -G Ninja \
        -DBUILD_APP=ON \
        -DBUILD_TESTING=OFF \
    && cmake --build build --parallel

CMD ["./build/ble_wp"]
