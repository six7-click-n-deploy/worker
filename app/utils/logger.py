"""
Structured and comprehensive logging utility for deployment tasks.
Provides detailed, JSON-serializable logging with categories, timing, and context.
Designed for backend integration with complete event tracking.
"""

import json
import logging
import re
import time
import traceback
from datetime import datetime
from enum import Enum
from typing import Any


class LogLevel(str, Enum):
    """Standard log levels"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogCategory(str, Enum):
    """Log categories for filtering and organization"""

    PHASE = "phase"  # Major phase transitions
    OPERATION = "operation"  # Specific operations (git, terraform, packer)
    SYSTEM = "system"  # System/infrastructure info
    STATUS = "status"  # Status updates
    OUTPUT = "output"  # Tool output (git, terraform, packer)
    ERROR = "error"  # Error information
    DEBUG = "debug"  # Debug information


def clean_text(text: str) -> str:
    """Remove ANSI escape codes and normalize text"""
    # Remove ANSI color codes
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    text = ansi_escape.sub("", text)

    # Normalize whitespace (single spaces, trim lines)
    text = text.strip()

    return text


def truncate_text(text: str, max_lines: int = 50, max_chars: int = 5000) -> str:
    """Truncate very long outputs intelligently"""
    # Truncate by character count first
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated, {len(text) - max_chars} more characters)"

    lines = text.split("\n")

    if len(lines) > max_lines:
        # Keep first 15 lines and last 20 lines
        kept = lines[:15] + [f"... (truncated {len(lines) - 35} lines) ..."] + lines[-20:]
        return "\n".join(kept)

    return text


class LogEntry:
    """Single structured log entry with complete metadata"""

    def __init__(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        category: LogCategory = LogCategory.SYSTEM,
        context: dict[str, Any] | None = None,
        operation: str | None = None,
        duration_ms: float | None = None,
    ):
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.message = clean_text(str(message))
        self.level = level
        self.category = category
        self.operation = operation
        self.duration_ms = duration_ms
        self.context = self._clean_context(context or {})

        # For long outputs, mark as truncated
        self.truncated = len(self.message) > 2000
        if self.truncated:
            self.message = truncate_text(self.message, max_lines=50, max_chars=5000)

    @staticmethod
    def _clean_context(context: dict[str, Any]) -> dict[str, Any]:
        """Clean context values for JSON serialization"""
        cleaned = {}
        for key, value in context.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                cleaned[key] = value
            elif isinstance(value, dict):
                cleaned[key] = LogEntry._clean_context(value)
            elif isinstance(value, (list, tuple)):
                cleaned[key] = [str(v) for v in value]
            else:
                cleaned[key] = str(value)
        return cleaned

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        entry_dict = {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "category": self.category.value,
            "message": self.message,
        }

        if self.operation:
            entry_dict["operation"] = self.operation

        if self.duration_ms is not None:
            entry_dict["duration_ms"] = round(self.duration_ms, 2)

        if self.truncated:
            entry_dict["truncated"] = True

        # Merge context
        entry_dict.update(self.context)

        return entry_dict

    def __str__(self) -> str:
        """Format for console output"""
        icon = self._get_icon()
        duration_str = f" [{self.duration_ms:.2f}ms]" if self.duration_ms else ""
        return f"{icon} [{self.timestamp}] {self.message}{duration_str}"

    def _get_icon(self) -> str:
        """Get emoji icon for log level"""
        icons = {
            LogLevel.DEBUG: "🔍",
            LogLevel.INFO: "ℹ️",
            LogLevel.SUCCESS: "✓",
            LogLevel.WARNING: "⚠️",
            LogLevel.ERROR: "❌",
        }
        return icons.get(self.level, "•")


class StructuredLogger:
    """Structured logger for tasks and operations with comprehensive tracking"""

    def __init__(self, name: str, track_timing: bool = True):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logs: list[LogEntry] = []
        self.track_timing = track_timing
        self._operation_stack: list[tuple[str, float]] = []  # Stack for nested operations

    # ============ Core Logging Methods ============

    def debug(self, message: str, category: LogCategory = LogCategory.DEBUG, **context) -> None:
        """Log debug message"""
        entry = LogEntry(message, LogLevel.DEBUG, category, context if context else None)
        self._record(entry)

    def info(self, message: str, category: LogCategory = LogCategory.SYSTEM, **context) -> None:
        """Log info message"""
        msg_str = str(message)
        if len(msg_str) > 5000:
            msg_str = truncate_text(msg_str, max_lines=50, max_chars=5000)
        entry = LogEntry(msg_str, LogLevel.INFO, category, context if context else None)
        self._record(entry)

    def success(self, message: str, category: LogCategory = LogCategory.STATUS, **context) -> None:
        """Log success message"""
        entry = LogEntry(message, LogLevel.SUCCESS, category, context if context else None)
        self._record(entry)

    def warning(self, message: str, category: LogCategory = LogCategory.SYSTEM, **context) -> None:
        """Log warning message"""
        entry = LogEntry(message, LogLevel.WARNING, category, context if context else None)
        self._record(entry)

    def error(self, message: str, category: LogCategory = LogCategory.ERROR, **context) -> None:
        """Log error message with optional stack trace"""
        entry = LogEntry(message, LogLevel.ERROR, category, context if context else None)
        self._record(entry)

    # ============ Structured Logging Methods ============

    def phase(self, phase_name: str) -> None:
        """Log a major deployment phase"""
        self.info(f"=== {phase_name.upper()} ===", category=LogCategory.PHASE)

    def operation_start(self, operation_name: str, **context) -> None:
        """Mark the start of an operation (with optional timing)"""
        if self.track_timing:
            self._operation_stack.append((operation_name, time.time()))

        self.info(f"Starting: {operation_name}", category=LogCategory.OPERATION, operation=operation_name, **context)

    def operation_end(self, operation_name: str, success: bool = True, **context) -> None:
        """Mark the end of an operation (with timing info)"""
        duration_ms = None

        if self.track_timing and self._operation_stack:
            last_op, start_time = self._operation_stack.pop()
            if last_op == operation_name:
                duration_ms = (time.time() - start_time) * 1000

        status = "completed" if success else "failed"
        self.info(
            f"{status.capitalize()}: {operation_name}",
            category=LogCategory.OPERATION,
            operation=operation_name,
            duration_ms=duration_ms,
            **context,
        )

    def command_output(self, tool_name: str, output: str, returncode: int = 0, **context) -> None:
        """Log command/tool output"""
        if returncode != 0:
            level = LogLevel.ERROR
            category = LogCategory.ERROR
        else:
            level = LogLevel.INFO
            category = LogCategory.OUTPUT

        # Truncate very long outputs
        if len(output) > 5000:
            output = truncate_text(output, max_lines=50, max_chars=5000)

        entry = LogEntry(
            output,
            level,
            category,
            {"tool": tool_name, "returncode": returncode, **(context if context else {})},
            operation=tool_name,
        )
        self._record(entry)

    def exception(self, message: str, exception: Exception | None = None, **context) -> None:
        """Log exception with optional stack trace"""
        error_context = {
            "exception_type": type(exception).__name__ if exception else "Unknown",
            **(context if context else {}),
        }

        if exception:
            error_context["exception_message"] = str(exception)
            # Add truncated stack trace
            tb = traceback.format_exc()
            if len(tb) > 2000:
                tb = truncate_text(tb, max_lines=30)
            error_context["stack_trace"] = tb

        entry = LogEntry(message, LogLevel.ERROR, LogCategory.ERROR, error_context)
        self._record(entry)

    def resource_info(self, resource_type: str, resource_name: str, **details) -> None:
        """Log information about a resource (deployment, repo, etc.)"""
        message = f"{resource_type}: {resource_name}"
        context = {"resource_type": resource_type, "resource_name": resource_name, **details}
        entry = LogEntry(message, LogLevel.INFO, LogCategory.SYSTEM, context)
        self._record(entry)

    # ============ Internal Recording ============

    def _record(self, entry: LogEntry) -> None:
        """Record log entry both to system logger and internal list"""
        self.logs.append(entry)

        # Also log to Python logger for application logs
        log_method = {
            LogLevel.SUCCESS: self.logger.info,
            LogLevel.ERROR: self.logger.error,
            LogLevel.WARNING: self.logger.warning,
            LogLevel.DEBUG: self.logger.debug,
            LogLevel.INFO: self.logger.info,
        }.get(entry.level, self.logger.info)

        log_method(str(entry))

    # ============ Output Methods ============

    def get_logs_dict(self) -> list[dict[str, Any]]:
        """Get all logs as list of dictionaries (for JSON APIs)"""
        return [log.to_dict() for log in self.logs]

    def get_logs_json(self, pretty: bool = True) -> str:
        """Get all logs as JSON string"""
        indent = 2 if pretty else None
        return json.dumps(self.get_logs_dict(), indent=indent)

    def get_logs_by_category(self, category: LogCategory) -> list[dict[str, Any]]:
        """Get logs filtered by category"""
        return [log.to_dict() for log in self.logs if log.category == category]

    def get_logs_by_level(self, level: LogLevel) -> list[dict[str, Any]]:
        """Get logs filtered by level"""
        return [log.to_dict() for log in self.logs if log.level == level]

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all logs"""
        by_level = {}
        by_category = {}

        for log in self.logs:
            # Count by level
            level_key = log.level.value
            by_level[level_key] = by_level.get(level_key, 0) + 1

            # Count by category
            cat_key = log.category.value
            by_category[cat_key] = by_category.get(cat_key, 0) + 1

        return {
            "total_entries": len(self.logs),
            "by_level": by_level,
            "by_category": by_category,
            "timestamp_range": {
                "first": self.logs[0].timestamp if self.logs else None,
                "last": self.logs[-1].timestamp if self.logs else None,
            },
        }

    def get_logs_text(self) -> str:
        """Get all logs formatted as plain text"""
        return "\n".join(str(log) for log in self.logs)

    def clear(self) -> None:
        """Clear all logs"""
        self.logs.clear()
        self._operation_stack.clear()


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance"""
    return StructuredLogger(name)
