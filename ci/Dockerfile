ARG OFFICIAL_IMAGE
FROM node:24.13.0-bookworm-slim@sha256:4660b1ca8b28d6d1906fd644abe34b2ed81d15434d26d845ef0aced307cf4b6f AS node_tools
WORKDIR /tools
COPY package*.json ./
RUN npm ci --ignore-scripts --omit=dev && node_modules/.bin/pi --version && node_modules/opencode-linux-x64/bin/opencode --version
ARG DROID_VERSION
RUN mkdir /droid-bin && if [ -n "$DROID_VERSION" ]; then \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && \
    droid_url="https://downloads.factory.ai/factory-cli/releases/$DROID_VERSION/linux/x64/droid" && \
    cd /droid-bin && curl --fail --silent --show-error --location "$droid_url" -o droid && \
    curl --fail --silent --show-error --location "$droid_url.sha256" -o droid.sha256 && \
    awk '{print $1 "  droid"}' droid.sha256 | sha256sum --check && \
    rm droid.sha256 && chmod 755 droid && ./droid --version; fi
FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv
FROM ${OFFICIAL_IMAGE}
USER root
COPY --from=node_tools /usr/local/bin/node /opt/harness/bin/node
COPY --from=node_tools /droid-bin/ /opt/harness/bin/
COPY --from=node_tools /tools/node_modules /opt/harness/node_modules
COPY --from=uv /uv /opt/harness/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/harness/python
RUN /opt/harness/bin/uv venv --python 3.12.12 /opt/harness/mini
COPY mini-requirements.txt /tmp/mini-requirements.txt
RUN /opt/harness/bin/uv pip sync --python /opt/harness/mini/bin/python /tmp/mini-requirements.txt && /opt/harness/mini/bin/mini-swe-agent --help >/dev/null
ENV PATH=/opt/harness/bin:/opt/harness/node_modules/.bin:/opt/harness/mini/bin:$PATH
