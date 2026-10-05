FROM golang:1.23 AS build
WORKDIR /src
COPY . .
RUN go build -o /queue ./cmd/queue

FROM gcr.io/distroless/static-debian12
COPY --from=build /queue /queue
ENTRYPOINT ["/queue"]
