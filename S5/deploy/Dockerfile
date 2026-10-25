# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# Client is generated from the vendored contract; rebuild it, then build the bundle.
RUN npm run codegen && npm run build

# ---- runtime stage ----
FROM nginx:1.27-alpine AS runtime
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/docker/entrypoint.sh /usr/local/bin/factory-entrypoint.sh
RUN chmod +x /usr/local/bin/factory-entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# Writes /usr/share/nginx/html/config.js from env (UI-APP-005) then serves.
ENTRYPOINT ["/usr/local/bin/factory-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
