# ESI-Bench

ESI-Bench is a lightweight, portable, and extensible system benchmarking platform. It allows users to benchmark their hardware components such as CPU, Memory, Disk, and Network.

## Author

-   Luis Javier Horcajada Torres - [LuisJavier.Horcajada\@alu.uclm.es](mailto:LuisJavier.Horcajada@alu.uclm.es)

## Prerequisites
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

## Available Benchmarks

1. **CPU Benchmark**: Tests integer performance (sieve of Eratosthenes), floating-point operations (Matrix Multiplication), and mixed workloads (Zlib Compression). Also tests multi-core scaling.
2. **Memory Benchmark**: Tests sequential bandwidth and random access latency.
3. **Disk Benchmark**: Tests sequential read/write speeds and random IOPS.
4. **Network Benchmark**: Tests ping latency, DNS resolution time, and internet bandwidth.
5. **FIWARE Benchmark**: Tests throughput, latency, and overall performance in an IoT ecosystem while using FIWARE and its components.

**More dashboards can be added in the `app/benchmarks` directory**

### Base Installation
1. Build and start the containers in the background:
   ```bash
   docker-compose -f docker-compose.yml up -d --build
   ```
2. Open your web browser and navigate to:
   ```text
   http://localhost:8000
   ```
3. To stop the application:
    ```text
    docker-compose down
    ```

### Local FIWARE Installation
1. Build and start the containers in the background:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.orion.yml -f docker-compose.fiware.yml up -d --build
   ```
2. Open your web browser and navigate to:
   ```text
   http://localhost:8000
   ```
3. To stop the application:
    
### Remote FIWARE Installation
1. Build and start the containers in the background in the device you want to benchmark:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.orion.yml up -d --build
   ```
2. Build and start FIWARE in the device that is going to send the entities:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.fiware.yml up -d --build
   ```
3. Select the FIWARE benchmark and select the IP of the device you want to benchmarki in the UI:
   ```text
   http://localhost:8000
   ```
4. To stop the application on both devices:
    ```text
    docker-compose down
    ```

