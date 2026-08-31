#!/usr/bin/env python3
"""
kora-http-server validator - static checks for Kora 2.0 HTTP controllers and interceptors.

Read-only: the script never writes, moves or rewrites anything. It parses the file
textually and reports findings, so it is always safe to run.

Catches the Kora 1.x -> 2.0 regressions that compile cleanly and fail silently:
  - @Tag(HttpServerModule.class) on a server-scoped interceptor (never invoked in 2.0)
  - an untagged, un-applied interceptor (dropped from the graph the same way)
  - ru.tinkoff.kora imports, and 2.0 types imported from their 1.x subpackage
  - Context parameters / usages (Context was removed from the whole framework)
  - suspend / Mono / Flux / CompletionStage handler contracts
  - toByteArrayUnchecked, and try/catch (IOException) around toByteArray
  - @Mapping(X.class) where X is declared in this file without @Component
  - a Kotlin @HttpController prefix whose slashes do not compose the way Java's would
  - stale httpServer.publicApiHttpPort / privateApiHttpPort keys in embedded config

Usage:
    python3 validate_controller.py path/to/UserController.java
    python3 validate_controller.py --json path/to/Interceptor.kt
    python3 validate_controller.py --verbose path/to/Controller.kt
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import List

# ---------------------------------------------------------------------------
# Kora 2.0 coordinates
# ---------------------------------------------------------------------------

SERVER_TAG_JAVA = "@Tag(HttpServer.class)"
SERVER_TAG_KOTLIN = "@Tag(HttpServer::class)"

# 2.0 packages for types that lived directly in http.server.common in 1.x
MOVED_TYPES = {
    "HttpServerRequest": "io.koraframework.http.server.common.request.HttpServerRequest",
    "HttpServerRequestMapper": "io.koraframework.http.server.common.request.HttpServerRequestMapper",
    "HttpServerResponse": "io.koraframework.http.server.common.response.HttpServerResponse",
    "HttpServerResponseException": "io.koraframework.http.server.common.response.HttpServerResponseException",
    "HttpServerResponseMapper": "io.koraframework.http.server.common.response.HttpServerResponseMapper",
    "HttpServerInterceptor": "io.koraframework.http.server.common.interceptor.HttpServerInterceptor",
}

STALE_CONFIG_KEYS = {
    "publicApiHttpPort": "httpServer.port",
    "privateApiHttpPort": "httpServer.system.port",
    "privateApiHttpMetricsPath": "httpServer.system.metricsPath",
    "privateApiHttpReadinessPath": "httpServer.system.readinessPath",
    "privateApiHttpLivenessPath": "httpServer.system.livenessPath",
    "virtualThreadsEnabled": "removed - virtual threads are always on in 2.0",
    "blockingThreads": "removed - virtual threads are always on in 2.0",
}


def strip_comments(content: str) -> str:
    """Blank out comments, keeping string literals and every offset intact.

    A legacy pattern named inside a doc comment is documentation, not code - these
    templates deliberately name @Tag(HttpServerModule) in order to warn about it, and
    flagging that would be a false positive. String literals are preserved so the route
    path and embedded-config checks still see them. Comment characters are replaced
    one-for-one and newlines are kept, so reported line numbers stay correct.
    """
    out = list(content)
    i, n = 0, len(content)
    while i < n:
        c = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if c == "/" and nxt == "/":                        # line comment
            while i < n and content[i] != "\n":
                out[i] = " "
                i += 1
            continue

        if c == "/" and nxt == "*":                        # block comment (Kotlin allows nesting)
            depth = 0
            while i < n:
                if content[i] == "/" and i + 1 < n and content[i + 1] == "*":
                    depth += 1
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                if content[i] == "*" and i + 1 < n and content[i + 1] == "/":
                    depth -= 1
                    out[i] = out[i + 1] = " "
                    i += 2
                    if depth <= 0:
                        break
                    continue
                if content[i] != "\n":
                    out[i] = " "
                i += 1
            continue

        if content.startswith('"""', i):                   # Kotlin raw string - skip over, keep
            i += 3
            while i < n and not content.startswith('"""', i):
                i += 1
            i += 3
            continue

        if c in ('"', "'"):                                # string / char literal - skip over, keep
            quote = c
            i += 1
            while i < n and content[i] != quote:
                if content[i] == "\\":
                    i += 2
                    continue
                if content[i] == "\n":
                    break
                i += 1
            i += 1
            continue

        i += 1
    return "".join(out)


@dataclass
class ValidationResult:
    file: str
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def line_of(content: str, index: int) -> int:
    return content[:index].count("\n") + 1


def is_kotlin(path: str, content: str) -> bool:
    return path.endswith((".kt", ".kt.template")) or "fun " in content


def is_controller(content: str) -> bool:
    return "@HttpController" in content


def is_interceptor(content: str) -> bool:
    return ("implements HttpServerInterceptor" in content
            or re.search(r":\s*HttpServerInterceptor\b", content) is not None)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_legacy_group(content: str) -> List[str]:
    errors = []
    for m in re.finditer(r"\bru\.tinkoff\.kora[\w.]*", content):
        errors.append(
            f"Line {line_of(content, m.start())}: Kora 1.x package '{m.group(0)}' - "
            f"Kora 2.0 uses io.koraframework.*"
        )
    return errors[:8]


def check_moved_types(content: str) -> List[str]:
    """2.0 split request/response/interceptor into their own subpackages."""
    errors = []
    for m in re.finditer(r"^\s*import\s+(io\.koraframework\.http\.server\.common\.(\w+));?\s*$",
                         content, re.MULTILINE):
        simple = m.group(2)
        if simple in MOVED_TYPES:
            errors.append(
                f"Line {line_of(content, m.start())}: '{m.group(1)}' does not exist in Kora 2.0 - "
                f"the type moved to '{MOVED_TYPES[simple]}'"
            )
    return errors


def check_interceptor_tag(content: str, kotlin: bool) -> (List[str], List[str]):
    """The single most dangerous 2.0 regression: the old tag compiles and never runs."""
    errors, warnings = [], []
    if not is_interceptor(content):
        return errors, warnings

    for m in re.finditer(r"@Tag\s*\(\s*HttpServerModule\s*(?:\.class|::class)\s*\)", content):
        errors.append(
            f"Line {line_of(content, m.start())}: @Tag(HttpServerModule) is the Kora 1.x tag. "
            f"It still COMPILES in 2.0 but nothing collects interceptors by it, so this "
            f"interceptor is pruned from the graph and silently never runs. Use "
            f"{SERVER_TAG_KOTLIN if kotlin else SERVER_TAG_JAVA}."
        )

    has_server_tag = re.search(r"@Tag\s*\(\s*HttpServer\s*(?:\.class|::class)\s*\)", content) is not None
    has_intercept_with = "@InterceptWith" in content
    if not has_server_tag and not errors and not has_intercept_with:
        warnings.append(
            "Interceptor is neither tagged nor applied. An untagged @Component interceptor is "
            "dropped from the graph exactly like the 1.x tag. Add "
            f"{SERVER_TAG_KOTLIN if kotlin else SERVER_TAG_JAVA} for server scope, or apply it "
            "with @InterceptWith on a controller or route."
        )
    return errors, warnings


def check_removed_context(content: str) -> List[str]:
    errors = []
    for m in re.finditer(r"\bContext\s*\.\s*(current|key|Key)\b|\bContext\s+\w+\s*[,)]", content):
        errors.append(
            f"Line {line_of(content, m.start())}: Kora's Context was removed from the entire "
            f"framework in 2.0. Use request.toBuilder() + @Header, a ScopedValue, or Principal."
        )
    return errors[:5]


def check_async_contracts(content: str, kotlin: bool) -> List[str]:
    errors = []
    for m in re.finditer(r"\bCompletionStage\s*<", content):
        errors.append(
            f"Line {line_of(content, m.start())}: CompletionStage is not a Kora 2.0 contract - "
            f"handlers and interceptors are synchronous and run on virtual threads."
        )
    for m in re.finditer(r"\b(Mono|Flux)\s*<", content):
        errors.append(
            f"Line {line_of(content, m.start())}: {m.group(1)} is not a Kora 2.0 contract - "
            f"http-server-common ships no reactive HttpServerResponseMapper."
        )
    if kotlin:
        for m in re.finditer(r"\bsuspend\s+fun\b", content):
            errors.append(
                f"Line {line_of(content, m.start())}: suspend is rejected by the Kora 2.0 KSP "
                f"processor ('Suspend methods are not supported by the HTTP server controller "
                f"generator'). Use StructuredTaskScope for concurrency."
            )
    return errors[:8]


def check_json_writer_usage(content: str) -> List[str]:
    errors = []
    for m in re.finditer(r"\btoByteArrayUnchecked\b|\btoStringUnchecked\b|\breadUnchecked\b", content):
        errors.append(
            f"Line {line_of(content, m.start())}: '{m.group(0)}' was removed in Kora 2.0 - "
            f"the plain method no longer declares a checked exception."
        )
    if "toByteArray" in content and re.search(r"catch\s*\(\s*(final\s+)?IOException", content):
        m = re.search(r"catch\s*\(\s*(final\s+)?IOException", content)
        errors.append(
            f"Line {line_of(content, m.start())}: catching IOException around JsonWriter.toByteArray "
            f"is a compile error in Kora 2.0 ('exception IOException is never thrown in body of "
            f"corresponding try statement') - remove the catch."
        )
    return errors


def check_mapping_components(content: str) -> List[str]:
    """@Mapping(X.class) injects the concrete class, so X must be a @Component."""
    warnings = []
    referenced = set(re.findall(r"@Mapping\s*\(\s*(?:[\w.]*\.)?(\w+)\s*(?:\.class|::class)\s*\)", content))
    for name in sorted(referenced):
        decl = re.search(
            r"((?:@\w+(?:\([^)]*\))?\s*)*)\b(?:public\s+|final\s+|static\s+|open\s+|inner\s+)*"
            r"(?:class|object)\s+" + re.escape(name) + r"\b",
            content)
        if decl and "@Component" not in decl.group(1):
            warnings.append(
                f"Line {line_of(content, decl.start())}: '{name}' is referenced by @Mapping but is "
                f"declared here without @Component. @Mapping injects the concrete class, so it must "
                f"be a graph component even with no constructor arguments, otherwise the build fails "
                f"with 'No component found for dependency: {name}'."
            )
    return warnings


def check_controller_shape(content: str, kotlin: bool) -> (List[str], List[str]):
    """@HttpController(value) IS a supported path prefix in 2.0 - only its shape is checked.

    Java normalises the join (adds a leading '/', strips a trailing one); Kotlin concatenates
    "$rootPath$path" raw, so a prefix without a leading slash, or with a trailing one, silently
    produces a different route than the same source compiled as Java.
    """
    errors, warnings = [], []
    if "@HttpRoute" not in content:
        errors.append("No @HttpRoute methods found on this @HttpController")

    m = re.search(r'@HttpController\s*\(\s*"([^"]*)"', content)
    if m and kotlin:
        prefix = m.group(1)
        if prefix and not prefix.startswith("/"):
            warnings.append(
                f'Line {line_of(content, m.start())}: @HttpController("{prefix}") has no leading '
                f"slash. The Kotlin processor concatenates the prefix and the route path verbatim, "
                f'so this yields "{prefix}<route>" instead of "/{prefix}<route>". Java would '
                f"normalise it - write the prefix as \"/{prefix}\"."
            )
        if len(prefix) > 1 and prefix.endswith("/"):
            warnings.append(
                f'Line {line_of(content, m.start())}: @HttpController("{prefix}") ends with a slash. '
                f"The Kotlin processor does not strip it, so routes get a doubled '//'. Java would "
                f"strip it - drop the trailing slash."
            )
    return errors, warnings


def check_path_parameter_match(content: str) -> List[str]:
    warnings = []
    routes = re.findall(r'@HttpRoute\([^)]*path\s*=\s*"([^"]+)"', content)
    explicit = set(re.findall(r'@Path\("([^"]+)"\)', content))
    implicit = set(re.findall(r'@Path\s+(?:final\s+)?[\w<>,.\[\]]+\s+(\w+)', content))
    implicit |= set(re.findall(r'@Path\s+(\w+)\s*:', content))          # Kotlin: @Path name: Type
    known = explicit | implicit

    for route_path in routes:
        for var in re.findall(r"\{([^}]+)\}", route_path):
            if var not in known:
                warnings.append(
                    f'Path variable {{{var}}} in "{route_path}" has no matching @Path argument '
                    f'(expected @Path("{var}") or an argument named \'{var}\')'
                )
    return warnings


def check_json_annotation(content: str) -> List[str]:
    warnings = []
    for m in re.finditer(r'@HttpRoute\([^)]*method\s*=\s*(?:HttpMethod\.)?"?(POST|PUT|PATCH)"?', content):
        chunk = content[m.start():m.start() + 500]
        if "@Json" not in chunk:
            warnings.append(
                f"Line {line_of(content, m.start())}: {m.group(1)} route may be missing @Json - "
                f"a JSON body needs @Json on the method and on the body parameter"
            )
    return warnings


def check_stale_config_keys(content: str) -> List[str]:
    warnings = []
    for key, replacement in STALE_CONFIG_KEYS.items():
        for m in re.finditer(r"\b" + re.escape(key) + r"\b", content):
            warnings.append(
                f"Line {line_of(content, m.start())}: '{key}' is a Kora 1.x config key. In 2.0 it is "
                f"an unknown key: ignored without warning, so the server silently falls back to its "
                f"default port. Use: {replacement}"
            )
            break
    return warnings


# ---------------------------------------------------------------------------

def validate_file(path: str) -> ValidationResult:
    result = ValidationResult(file=path, valid=True)

    content = read_file(path)
    if not content:
        result.valid = False
        result.errors.append(f"Cannot read file (missing or empty): {path}")
        return result

    content = strip_comments(content)

    kotlin = is_kotlin(path, content)
    controller = is_controller(content)
    interceptor = is_interceptor(content)

    if not controller and not interceptor:
        result.warnings.append(
            "File contains neither @HttpController nor an HttpServerInterceptor implementation - "
            "only the generic Kora 2.0 checks were applied"
        )

    result.errors.extend(check_legacy_group(content))
    result.errors.extend(check_moved_types(content))
    result.errors.extend(check_removed_context(content))
    result.errors.extend(check_async_contracts(content, kotlin))
    result.errors.extend(check_json_writer_usage(content))
    result.warnings.extend(check_mapping_components(content))
    result.warnings.extend(check_stale_config_keys(content))

    if controller:
        errs, warns = check_controller_shape(content, kotlin)
        result.errors.extend(errs)
        result.warnings.extend(warns)
        result.warnings.extend(check_path_parameter_match(content))
        result.warnings.extend(check_json_annotation(content))
        result.info.append(f"Found {len(re.findall(r'@HttpRoute', content))} HTTP route(s)")

    if interceptor:
        errs, warns = check_interceptor_tag(content, kotlin)
        result.errors.extend(errs)
        result.warnings.extend(warns)
        result.info.append("Detected an HttpServerInterceptor implementation")

    result.info.append("Language: Kotlin" if kotlin else "Language: Java")

    if result.errors:
        result.valid = False
    return result


def print_human_readable(result: ValidationResult, verbose: bool = False):
    print(f"{'OK  ' if result.valid else 'FAIL'} {result.file}")
    for error in result.errors:
        print(f"  ERROR:   {error}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    if verbose:
        for info in result.info:
            print(f"  INFO:    {info}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate Kora 2.0 HTTP controllers and interceptors (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s src/main/java/com/example/UserController.java
  %(prog)s --json src/main/kotlin/com/example/LoggingInterceptor.kt
  %(prog)s --verbose src/main/java/com/example/ErrorInterceptor.java

Exit code 0 when no errors were found, 1 otherwise. Warnings do not fail the run.
This script only reads the files it is given; it never modifies anything.
""",
    )
    parser.add_argument("path", nargs="+", help="Java/Kotlin controller or interceptor file(s)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include INFO lines")

    args = parser.parse_args()

    results = [validate_file(p) for p in args.path]

    if args.json:
        payload = [asdict(r) for r in results]
        print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))
    else:
        for r in results:
            print_human_readable(r, args.verbose)

    sys.exit(0 if all(r.valid for r in results) else 1)


if __name__ == "__main__":
    main()
