# ---------------------------------------------------------------------------
# Frontend image: a MULTI-STAGE build.
#
# Stage 1 ("build") has Node.js and runs `vite build`, producing static files in dist/.
# Stage 2 copies only dist/ into a tiny nginx image. Node, node_modules and the
# source never reach the final image, so it is ~50 MB instead of ~1 GB.
#
# Build:  docker build -t mychatapp-frontend:1.0 ./frontend
# Run:    docker run -p 8080:80 mychatapp-frontend:1.0
# ---------------------------------------------------------------------------

# ---- Stage 1: build the static site ----
FROM node:22-alpine AS build
WORKDIR /app

# Install dependencies first so this layer is cached until package*.json change.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite bakes VITE_* variables into the JavaScript at BUILD time (not at run time).
# "/api" is a relative URL: the browser calls the same server that served the page,
# and nginx (stage 2) forwards /api/* to the backend. No CORS needed.
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

# ---- Stage 2: serve the files with nginx ----
FROM nginx:1.27-alpine

# Our nginx config: serve the site + proxy /api to the backend.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Take only the built files from stage 1.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
# nginx:alpine already defines CMD ["nginx", "-g", "daemon off;"]
