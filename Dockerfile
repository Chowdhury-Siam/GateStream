FROM golang:1.25 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags="-s -w" -o /gatestream .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates && addgroup -g 1000 bot && adduser -D -u 1000 -G bot bot
WORKDIR /app
COPY --from=build /gatestream /usr/local/bin/gatestream
COPY third_party_licenses/ /usr/share/doc/gatestream/third_party_licenses/
RUN chown bot:bot /app
USER bot
ENV BIND_ADDRESS=0.0.0.0 PORT=8080
EXPOSE 8080
ENTRYPOINT ["gatestream"]
