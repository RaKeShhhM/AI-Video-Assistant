FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["node", "src/index.js"]
