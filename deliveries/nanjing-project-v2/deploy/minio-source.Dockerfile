FROM docker.m.daocloud.io/golang:1.21.5-alpine3.18@sha256:d8b99943fb0587b79658af03d4d4e8b57769b21dcf08a8401352a9f2a7228754 AS build
WORKDIR /src
COPY go.mod go.sum ./
ENV CGO_ENABLED=0 GOPROXY=https://goproxy.cn,direct
RUN go mod download
COPY . .
RUN go build -mod=readonly -trimpath -tags kqueue -ldflags "-s -w -X github.com/minio/minio/cmd.Version=2023-12-20T01:00:02Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2023-12-20T01-00-02Z -X github.com/minio/minio/cmd.CommitID=7a311a3b666a0e5476fbde24d4413220876d9df2 -X github.com/minio/minio/cmd.ShortCommitID=7a311a3b666a0 -X github.com/minio/minio/cmd.CopyrightYear=2023" -o /minio .
RUN GOPROXY=https://proxy.golang.org,https://goproxy.cn go install -trimpath -tags kqueue -ldflags "-s -w -X github.com/minio/mc/cmd.Version=2023-12-14T00:37:41Z -X github.com/minio/mc/cmd.ReleaseTag=RELEASE.2023-12-14T00-37-41Z -X github.com/minio/mc/cmd.CommitID=8da737f8fd63de58dc475bf88eb9e285d206e3ae -X github.com/minio/mc/cmd.ShortCommitID=8da737f8fd63d -X github.com/minio/mc/cmd.CopyrightYear=2023" github.com/minio/mc@v0.0.0-20231214003741-8da737f8fd63

FROM docker.m.daocloud.io/alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f
RUN apk add --no-cache ca-certificates curl
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="7a311a3b666a0e5476fbde24d4413220876d9df2" \
      io.ict.minio.client.revision="8da737f8fd63de58dc475bf88eb9e285d206e3ae" \
      org.opencontainers.image.version="RELEASE.2023-12-20T01-00-02Z-source"
COPY --from=build /minio /usr/bin/minio
COPY --from=build /go/bin/mc /usr/bin/mc
COPY dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
COPY LICENSE /usr/share/licenses/minio/LICENSE
RUN sed -i 's/\r$//' /usr/bin/docker-entrypoint.sh && chmod +x /usr/bin/docker-entrypoint.sh
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
