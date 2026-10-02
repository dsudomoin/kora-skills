#!/usr/bin/env python3
"""Validate the `kafka { ... }` section of a Kora 2.0 HOCON/YAML-ish config.

Checks a config against what Kora 2.0 actually reads:

  * `kafka.producer.<name>` -> KafkaPublisherConfig       (driverProperties, telemetry)
  * a `@Topic` path         -> KafkaPublisherConfig.TopicConfig       (topic, partition)
  * a transactional path    -> KafkaPublisherConfig.TransactionConfig (idPrefix,
                               maxPoolSize, maxWaitTime -- and NOTHING else)

Sections are classified by the keys they carry, because a publisher section, a topic
section and a transactional section are three different mappings that happen to live
side by side under `kafka.producer`. Kora ignores unknown keys inside a mapped section,
so a topic block nested inside a publisher block is valid and common; both layouts are
accepted here.

Read-only: this script never writes to the config.

Usage:
    python3 validate_config.py --config application.conf
    python3 validate_config.py --config application.conf --strict   # warnings fail too
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Keys Kora 2.0 reads. Anything else in these sections is dead config.
TRANSACTION_KEYS = {"idPrefix", "maxPoolSize", "maxWaitTime", "telemetry"}
TOPIC_KEYS = {"topic", "partition"}

# Serializers come from the DI graph; Kora always builds the KafkaProducer with
# ByteArraySerializer on both sides, so these driver properties are ignored.
FORBIDDEN_DRIVER_PROPS = {"key.serializer", "value.serializer"}

# 1.x spellings that must not survive a 2.0 migration.
STALE_PATTERNS = [
    (r"ru\.tinkoff\.kora", "Kora 1.x package: 2.0 uses io.koraframework.*"),
    (r"\bkora-parent\b", "Kora 1.x BOM: 2.0 uses io.koraframework:kora-bom"),
    (r"\bjson-module\b", "Kora 1.x artifact: 2.0 uses io.koraframework:json-common"),
]


class Node:
    """One config block: its direct keys and its child blocks."""

    __slots__ = ("path", "keys", "children")

    def __init__(self, path: str):
        self.path = path
        self.keys: dict[str, str | None] = {}
        self.children: dict[str, "Node"] = {}

    def child(self, name: str) -> "Node":
        node = self.children.get(name)
        if node is None:
            node = Node(f"{self.path}.{name}" if self.path else name)
            self.children[name] = node
        return node

    def descend(self, dotted: str) -> "Node | None":
        node = self
        for part in dotted.split("."):
            node = node.children.get(part)
            if node is None:
                return None
        return node

    def flat_keys(self) -> dict[str, str | None]:
        """Every key reachable from this node, as dotted paths relative to it."""
        out = dict(self.keys)
        for name, child in self.children.items():
            out[name] = None
            for key, value in child.flat_keys().items():
                out[f"{name}.{key}"] = value
        return out


def _blank_comments(text: str) -> str:
    """Replace comment bodies with spaces, preserving offsets. Quotes are respected."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/"):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def _skip_string(text: str, i: int) -> int:
    """Return the index just past the string literal starting at text[i] == '"'."""
    i += 1
    n = len(text)
    while i < n and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the '}' matching the '{' at open_idx (or len(text) if unbalanced)."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _name_before(text: str, brace_idx: int) -> tuple[list[str], bool]:
    """Read the block name preceding a '{'. Returns (path segments, found)."""
    i = brace_idx - 1
    while i >= 0 and text[i] in " \t\r\n":
        i -= 1
    if i >= 0 and text[i] in "=:":
        i -= 1
        while i >= 0 and text[i] in " \t\r\n":
            i -= 1
    if i < 0:
        return [], False
    if text[i] == '"':
        end = i
        i -= 1
        while i >= 0 and text[i] != '"':
            i -= 1
        # A quoted name is one segment even when it contains dots.
        return [text[i + 1 : end]], True
    end = i + 1
    while i >= 0 and (text[i].isalnum() or text[i] in "_-.$"):
        i -= 1
    raw = text[i + 1 : end]
    if not raw:
        return [], False
    return [p for p in raw.split(".") if p], True


_KEY_RE = re.compile(r'(?:"([^"]+)"|([A-Za-z_][\w\-.$]*))\s*[=:]\s*([^\n,}]*)')


def _parse(text: str, start: int, end: int, node: Node) -> None:
    """Populate `node` from the region text[start:end]."""
    i = start
    scalar_spans: list[tuple[int, int]] = []
    cursor = start
    while i < end:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "{":
            close = _match_brace(text, i)
            segments, found = _name_before(text, i)
            scalar_spans.append((cursor, i))
            if found:
                target = node
                for seg in segments:
                    target = target.child(seg)
                _parse(text, i + 1, min(close, end), target)
            i = close + 1
            cursor = i
            continue
        if c == "}":
            break
        i += 1
    scalar_spans.append((cursor, min(i, end)))

    for span_start, span_end in scalar_spans:
        chunk = text[span_start:span_end]
        for m in _KEY_RE.finditer(chunk):
            quoted, bare, value = m.group(1), m.group(2), m.group(3)
            value = value.strip()
            if quoted is not None:
                node.keys[quoted] = value
            else:
                parts = [p for p in bare.split(".") if p]
                if len(parts) == 1:
                    node.keys[parts[0]] = value
                else:
                    target = node
                    for seg in parts[:-1]:
                        target = target.child(seg)
                    target.keys[parts[-1]] = value


class Validator:
    def __init__(self, config_path: str, strict: bool = False):
        self.config_path = Path(config_path)
        self.strict = strict
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.text = ""
        self.root = Node("")

    def load(self) -> bool:
        if not self.config_path.exists():
            self.errors.append(f"Configuration file not found: {self.config_path}")
            return False
        try:
            self.text = self.config_path.read_text(encoding="utf-8")
        except OSError as e:
            self.errors.append(f"Failed to read config file: {e}")
            return False
        stripped = _blank_comments(self.text)
        _parse(stripped, 0, len(stripped), self.root)
        return True

    def check_stale(self) -> None:
        for pattern, message in STALE_PATTERNS:
            for m in re.finditer(pattern, self.text):
                line = self.text.count("\n", 0, m.start()) + 1
                self.errors.append(f"line {line}: {m.group(0)!r} -- {message}")

    def check_producers(self) -> None:
        producer = self.root.descend("kafka.producer")
        if producer is None:
            self.notes.append("No kafka.producer section found -- nothing to validate for publishers")
            return

        for name, section in sorted(producer.children.items()):
            keys = section.keys
            has_driver = "driverProperties" in section.children or "driverProperties" in keys
            has_topic = "topic" in keys or "partition" in keys
            tx_keys = {"idPrefix", "maxPoolSize", "maxWaitTime"} & set(keys)

            # Transactional wins over driverProperties on purpose: a transactional section
            # carrying a driverProperties block is dead config, and reporting that is the
            # whole point -- classifying it as a publisher would hide the mistake.
            if tx_keys:
                self._check_transactional(name, section)
            elif has_driver:
                self._check_publisher(name, section)
                # A topic block nested inside the publisher section is the @Topic(".x") layout.
                for sub_name, sub in sorted(section.children.items()):
                    if sub_name == "driverProperties":
                        continue
                    if "topic" in sub.keys:
                        self._check_topic(f"{name}.{sub_name}", sub)
            elif has_topic:
                self._check_topic(name, section)
            else:
                self.warnings.append(
                    f"kafka.producer.{name}: cannot classify this section -- it has no "
                    f"driverProperties (publisher), no topic (topic config) and no "
                    f"idPrefix/maxPoolSize/maxWaitTime (transactional)"
                )

    def _check_publisher(self, name: str, section: Node) -> None:
        driver = section.children.get("driverProperties")
        if driver is None:
            self.errors.append(f"kafka.producer.{name}: driverProperties must be a block")
            return
        props = driver.flat_keys()
        if "bootstrap.servers" not in props:
            self.errors.append(
                f"kafka.producer.{name}.driverProperties: missing required 'bootstrap.servers'"
            )
        for forbidden in sorted(FORBIDDEN_DRIVER_PROPS & set(props)):
            self.errors.append(
                f"kafka.producer.{name}.driverProperties: remove '{forbidden}' -- Kora always "
                f"uses ByteArraySerializer and applies the graph-resolved Serializer<T> itself"
            )
        if "min.insync.replicas" in props:
            self.warnings.append(
                f"kafka.producer.{name}.driverProperties: 'min.insync.replicas' is a broker/topic "
                f"setting, not a producer property -- it has no effect here"
            )
        self._check_telemetry(f"kafka.producer.{name}", section)

    def _check_topic(self, name: str, section: Node) -> None:
        if not section.keys.get("topic"):
            self.errors.append(f"kafka.producer.{name}: topic section is missing the required 'topic' key")
        partition = section.keys.get("partition")
        if partition is not None and partition != "" and not re.fullmatch(r"-?\d+", partition.strip('"')):
            self.errors.append(
                f"kafka.producer.{name}.partition: must be an integer, got {partition!r}"
            )
        unknown = set(section.keys) - TOPIC_KEYS
        for key in sorted(unknown):
            self.warnings.append(
                f"kafka.producer.{name}.{key}: not read by TopicConfig (only topic, partition)"
            )

    def _check_transactional(self, name: str, section: Node) -> None:
        if "driverProperties" in section.children:
            self.errors.append(
                f"kafka.producer.{name}.driverProperties: dead config -- a transactional section maps "
                f"to TransactionConfig (idPrefix, maxPoolSize, maxWaitTime only). Driver properties "
                f"come from the WRAPPED publisher's section and this block is silently ignored"
            )
        unknown = (set(section.keys) | set(section.children)) - TRANSACTION_KEYS
        for key in sorted(unknown):
            self.warnings.append(
                f"kafka.producer.{name}.{key}: not read by TransactionConfig "
                f"(only idPrefix, maxPoolSize, maxWaitTime)"
            )
        pool = section.keys.get("maxPoolSize")
        if pool is not None and not re.fullmatch(r"\d+", pool.strip('"')):
            self.errors.append(f"kafka.producer.{name}.maxPoolSize: must be an integer, got {pool!r}")
        self._check_telemetry(f"kafka.producer.{name}", section)

    def _check_telemetry(self, path: str, section: Node) -> None:
        telemetry = section.children.get("telemetry")
        flat = telemetry.flat_keys() if telemetry else {}
        for kind in ("logging", "metrics"):
            value = flat.get(f"{kind}.enabled")
            if value is None:
                self.notes.append(
                    f"{path}.telemetry.{kind}.enabled is unset and DEFAULTS TO FALSE in Kora 2.0 -- "
                    f"no producer {kind} will be emitted"
                )
            elif value.strip().strip('"').lower() == "false":
                self.notes.append(f"{path}.telemetry.{kind}.enabled = false -- {kind} disabled")

    def check_consumers(self) -> None:
        """Light sanity check only -- kora-kafka-consumer owns the consumer contract."""
        consumer = self.root.descend("kafka.consumer")
        if consumer is None:
            return
        for name, section in sorted(consumer.children.items()):
            keys = section.keys
            if "topics" not in keys and "topicsPattern" not in keys and "partitions" not in keys:
                self.errors.append(
                    f"kafka.consumer.{name}: missing 'topics', 'topicsPattern' or 'partitions'"
                )
            driver = section.children.get("driverProperties")
            if driver is None:
                self.errors.append(f"kafka.consumer.{name}: missing driverProperties")
                continue
            props = driver.flat_keys()
            if "bootstrap.servers" not in props:
                self.errors.append(
                    f"kafka.consumer.{name}.driverProperties: missing required 'bootstrap.servers'"
                )

    def run(self) -> bool:
        if not self.load():
            self._report()
            return False

        print(f"Validating: {self.config_path}")
        print("Kora 2.0 (io.koraframework) Kafka producer configuration")
        print("-" * 62)

        self.check_stale()
        self.check_producers()
        self.check_consumers()
        self._report()

        failed = bool(self.errors) or (self.strict and bool(self.warnings))
        return not failed

    def _report(self) -> None:
        if self.errors:
            print("\nERRORS:")
            for item in self.errors:
                print(f"  - {item}")
        if self.warnings:
            print("\nWARNINGS:")
            for item in self.warnings:
                print(f"  - {item}")
        if self.notes:
            print("\nNOTES:")
            for item in self.notes:
                print(f"  - {item}")
        if not self.errors and not self.warnings:
            print("\nNo problems found.")
        print("-" * 62)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Kora 2.0 Kafka configuration")
    parser.add_argument("--config", required=True, help="Path to application.conf")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    args = parser.parse_args()

    return 0 if Validator(args.config, args.strict).run() else 1


if __name__ == "__main__":
    sys.exit(main())
