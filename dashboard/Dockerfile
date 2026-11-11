# Runtime and base filesystem pinned together; update deliberately with package-lock.json.
FROM node:22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Container-only output configuration. Do not edit/restart the host's running next dev.
RUN mv next.config.ts next.config.base.mts \
    && printf "import base from './next.config.base.mts';\nexport default { ...base, output: 'standalone' };\n" > next.config.mjs
ENV DATA_MODE=real LOCAL_DATA_API_URL=http://sports-data:8002
RUN npm run build -- --webpack

FROM node:22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
# Without a session: the access route (503 if access is not configured), the backend and real mode.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 CMD node -e "Promise.all([fetch('http://127.0.0.1:3000/api/v1/session'),fetch(process.env.LOCAL_DATA_API_URL+'/health')]).then(async ([a,b])=>{if(!a.ok||!b.ok||process.env.DATA_MODE!=='real'||(await a.json()).required!==true)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
