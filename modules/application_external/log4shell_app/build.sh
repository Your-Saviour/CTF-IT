#!/bin/bash
set -e

BUILD_DIR=$(mktemp -d)
SRC_DIR="$BUILD_DIR/src/main/java/com/ctf/logapp"
mkdir -p "$SRC_DIR"

# --- pom.xml ---
cat > "$BUILD_DIR/pom.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>2.7.18</version>
        <relativePath/>
    </parent>
    <groupId>com.ctf</groupId>
    <artifactId>logapp</artifactId>
    <version>1.0</version>
    <packaging>jar</packaging>
    <properties>
        <java.version>17</java.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
            <exclusions>
                <exclusion>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-logging</artifactId>
                </exclusion>
            </exclusions>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-log4j2</artifactId>
        </dependency>
        <!-- Pinned vulnerable version - CVE-2021-44228 -->
        <dependency>
            <groupId>org.apache.logging.log4j</groupId>
            <artifactId>log4j-core</artifactId>
            <version>2.14.1</version>
        </dependency>
        <dependency>
            <groupId>org.apache.logging.log4j</groupId>
            <artifactId>log4j-api</artifactId>
            <version>2.14.1</version>
        </dependency>
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
                <configuration>
                    <finalName>logapp-1.0</finalName>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
EOF

# --- LogAppApplication.java ---
cat > "$SRC_DIR/LogAppApplication.java" << 'EOF'
package com.ctf.logapp;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class LogAppApplication {
    public static void main(String[] args) {
        SpringApplication.run(LogAppApplication.class, args);
    }
}
EOF

# --- LogEntry.java ---
cat > "$SRC_DIR/LogEntry.java" << 'EOF'
package com.ctf.logapp;

public class LogEntry {
    private String message;
    private String level;

    public LogEntry() {}

    public LogEntry(String message, String level) {
        this.message = message;
        this.level = level;
    }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
    public String getLevel() { return level; }
    public void setLevel(String level) { this.level = level; }
}
EOF

# --- LogController.java ---
cat > "$SRC_DIR/LogController.java" << 'EOF'
package com.ctf.logapp;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Collections;
import java.util.List;
import java.util.Map;

@RestController
public class LogController {

    private static final Logger logger = LogManager.getLogger(LogController.class);

    @Value("${logging.file.name:/var/log/logapp/application.log}")
    private String logFilePath;

    @PostMapping("/log")
    public ResponseEntity<Map<String, String>> log(@RequestBody LogEntry request) {
        String level = request.getLevel() != null ? request.getLevel().toUpperCase() : "INFO";
        // Vulnerable: user input passed directly to Log4j triggers JNDI lookup processing
        switch (level) {
            case "WARN":  logger.warn("Received: " + request.getMessage());  break;
            case "ERROR": logger.error("Received: " + request.getMessage()); break;
            case "DEBUG": logger.debug("Received: " + request.getMessage()); break;
            default:      logger.info("Received: " + request.getMessage());
        }
        return ResponseEntity.ok(Map.of("status", "logged", "level", level));
    }

    @GetMapping("/logs")
    public ResponseEntity<List<String>> logs() {
        try {
            List<String> lines = Files.readAllLines(Paths.get(logFilePath));
            int size = lines.size();
            return ResponseEntity.ok(lines.subList(Math.max(0, size - 50), size));
        } catch (IOException e) {
            return ResponseEntity.ok(Collections.emptyList());
        }
    }
}
EOF

# Build the fat JAR
cd "$BUILD_DIR"
mvn package -DskipTests -q

# Install the JAR
cp "$BUILD_DIR/target/logapp-1.0.jar" /opt/logapp/logapp.jar

# Clean up build artefacts
rm -rf "$BUILD_DIR"
