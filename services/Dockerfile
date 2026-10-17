# Una sola imagen con todos los mocks del lab (/svc/<nombre>); cada Deployment
# elige el binario con `command`. Un build, un push, un `k3d image import`.
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN for svc in $(ls cmd); do \
      CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/$svc ./cmd/$svc || exit 1; \
    done

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ /svc/
USER nonroot:nonroot
