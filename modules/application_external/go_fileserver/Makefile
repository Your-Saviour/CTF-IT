.PHONY: build clean

build:
	GOOS=linux GOARCH=amd64 go build -o bin/fileserver-linux-amd64 ./src/
	GOOS=linux GOARCH=arm64 go build -o bin/fileserver-linux-arm64 ./src/

clean:
	rm -f bin/fileserver-linux-amd64 bin/fileserver-linux-arm64
