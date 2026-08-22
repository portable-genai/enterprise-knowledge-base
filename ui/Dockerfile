# Production UI image: reproducible Next.js build, non-root runtime, same-origin API.
FROM node:22.22.0-bookworm-slim@sha256:fa7c057d6f8017cc7b53fda08b3a77cec3f55f4be3e21614b2b7bbff8fb3c21a AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_KB_API_URL=""
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN mkdir -p public && npm run build && npm prune --omit=dev

FROM node:22.22.0-bookworm-slim@sha256:fa7c057d6f8017cc7b53fda08b3a77cec3f55f4be3e21614b2b7bbff8fb3c21a AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_KB_API_URL="" PORT=3000 HOSTNAME=0.0.0.0
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder --chown=appuser:appuser /app/.next ./.next
COPY --from=builder --chown=appuser:appuser /app/node_modules ./node_modules
COPY --from=builder --chown=appuser:appuser /app/package.json ./package.json
COPY --from=builder --chown=appuser:appuser /app/public ./public
USER appuser
EXPOSE 3000
CMD ["npm", "run", "start"]
