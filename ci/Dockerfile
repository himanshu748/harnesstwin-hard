ARG OFFICIAL_IMAGE
FROM node:24.13.0-bookworm-slim AS node_tools
WORKDIR /tools
COPY package*.json ./
RUN npm ci --ignore-scripts --omit=dev && node_modules/.bin/pi --version && node_modules/opencode-linux-x64/bin/opencode --version
FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM ${OFFICIAL_IMAGE}
USER root
COPY --from=node_tools /usr/local/bin/node /opt/harness/bin/node
COPY --from=node_tools /tools/node_modules /opt/harness/node_modules
COPY --from=uv /uv /opt/harness/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/harness/python
RUN /opt/harness/bin/uv venv --python 3.12.12 /opt/harness/mini
COPY mini-requirements.txt /tmp/mini-requirements.txt
RUN /opt/harness/bin/uv pip install --python /opt/harness/mini/bin/python -r /tmp/mini-requirements.txt && /opt/harness/mini/bin/mini-swe-agent --help >/dev/null
ENV PATH=/opt/harness/bin:/opt/harness/node_modules/.bin:/opt/harness/mini/bin:$PATH
