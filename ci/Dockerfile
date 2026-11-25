ARG OFFICIAL_IMAGE
FROM node:24.13.0-bookworm-slim@sha256:4660b1ca8b28d6d1906fd644abe34b2ed81d15434d26d845ef0aced307cf4b6f AS node_tools
WORKDIR /tools
COPY package*.json ./
RUN npm ci --ignore-scripts --omit=dev && node_modules/.bin/pi --version && node_modules/opencode-linux-x64/bin/opencode --version
FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv
FROM ${OFFICIAL_IMAGE}
USER root
COPY --from=node_tools /usr/local/bin/node /opt/harness/bin/node
COPY --from=node_tools /tools/node_modules /opt/harness/node_modules
COPY --from=uv /uv /opt/harness/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/harness/python
RUN /opt/harness/bin/uv venv --python 3.12.12 /opt/harness/mini
COPY mini-requirements.txt /tmp/mini-requirements.txt
RUN /opt/harness/bin/uv pip sync --python /opt/harness/mini/bin/python -r /tmp/mini-requirements.txt && /opt/harness/mini/bin/mini-swe-agent --help >/dev/null
ENV PATH=/opt/harness/bin:/opt/harness/node_modules/.bin:/opt/harness/mini/bin:$PATH
