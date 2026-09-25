# Gateway: Caddy + landing/docs/version + public artifacts (submission)
FROM caddy:2.8-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY landing /srv/landing
COPY docs /srv/docs
COPY dist/version.json /srv/version.json
COPY artifacts/public /srv/artifacts
LABEL org.opencontainers.image.title="MosTrans Predict Gateway"
EXPOSE 80 443
