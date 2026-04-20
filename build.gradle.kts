plugins {
    application
    java
}

repositories {
    mavenCentral()
    maven {
        name = "SciJava"
        url = uri("https://maven.scijava.org/content/repositories/releases")
    }
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
}

dependencies {
    implementation("org.apposed:appose:0.11.0")
    implementation("org.slf4j:slf4j-simple:2.0.13")
}

application {
    mainClass.set("io.github.michaelsnelson.repro.ApposeDataLoaderRepro")
}

tasks.withType<JavaExec>().configureEach {
    // Forward CLI args -- usage: ./gradlew run --args="60"
    // Default timeout for the num_workers=2 case is in the Java file.
    systemProperty("org.slf4j.simpleLogger.defaultLogLevel", "info")
}
