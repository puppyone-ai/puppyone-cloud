# Hosted frontend image, built by the `images` job in
# .github/workflows/_environment-release.yml.
#
# Next.js inlines NEXT_PUBLIC_* values at build time, so this image is built
# once per commit *per environment* (the environment's public endpoints are
# build arguments). Server-only values such as API_INTERNAL_URL stay runtime
# variables on the hosting service. Dockerfile.local remains the
# self-hosted/Compose image.

FROM node:22.22.3-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
COPY packages/cloud-core ./packages/cloud-core
RUN npm ci

FROM node:22.22.3-bookworm-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_APP_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_AUTH_EMAIL_ONLY
ARG NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS
ARG NEXT_PUBLIC_DEV_MODE
ARG APP_VERSION

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY \
    NEXT_PUBLIC_AUTH_EMAIL_ONLY=$NEXT_PUBLIC_AUTH_EMAIL_ONLY \
    NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS=$NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS \
    NEXT_PUBLIC_DEV_MODE=$NEXT_PUBLIC_DEV_MODE \
    APP_VERSION=$APP_VERSION \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:22.22.3-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

ARG SOURCE_SHA=unknown
LABEL org.opencontainers.image.source="https://github.com/puppyone-ai/puppyone-cloud" \
      org.opencontainers.image.revision=$SOURCE_SHA

USER node
EXPOSE 3000
CMD ["node", "server.js"]
