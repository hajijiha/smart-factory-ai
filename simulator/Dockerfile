FROM osrf/ros:humble-desktop-full
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONPATH=/app DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 GAZEBO_MODEL_DATABASE_URI=""
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-gazebo-ros-pkgs libgazebo-dev nlohmann-json3-dev xvfb mesa-utils python3-pip python3-scipy python3-opencv && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir paho-mqtt==2.1.0 PyYAML==6.0.2
WORKDIR /app
COPY . .
RUN . /opt/ros/humble/setup.sh && cmake -S simulator/plugin -B /tmp/factory-build && cmake --build /tmp/factory-build -j4 && cp /tmp/factory-build/libfactory_world.so /app/simulator/
RUN python3 -c 'import hashlib,json,pathlib; root=pathlib.Path("/app"); source=root/"simulator/plugin/world.cpp"; library=root/"simulator/libfactory_world.so"; value={"schema_version":1,"source_path":"simulator/plugin/world.cpp","library_path":"simulator/libfactory_world.so","source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"library_sha256":hashlib.sha256(library.read_bytes()).hexdigest()}; (root/"simulator/plugin-build.json").write_text(json.dumps(value,sort_keys=True)+"\n")'
ENV GAZEBO_PLUGIN_PATH=/app/simulator:/opt/ros/humble/lib
ENTRYPOINT ["/bin/bash", "/app/simulator/start.sh"]
