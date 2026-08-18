# core/config.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol
import yaml


class RateLimiter(Protocol):
    def allow(self, action) -> bool: ...


@dataclass
class CapabilityConfig:
    enabled: bool = False
    confidence_threshold: float = 1.0
    rate_limit_per_hour: int = 60


@dataclass
class MaintainerConfig:
    """
    Loaded from .ai-maintainer.yaml at startup.
    Fails startup on malformed config — never fails open.
    """
    kill_switch_active: bool = False
    capabilities: dict[str, dict[str, CapabilityConfig]] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)
    rate_limiter: RateLimiter = None  # injected at construction time

    def capability_enabled(self, capability_key: str) -> bool:
        """
        capability_key is 'plugin.action', e.g. 'dep_pr.auto_merge'.
        Returns False if the plugin or action key is absent — fail closed.
        """
        parts = capability_key.split(".", 1)
        if len(parts) != 2:
            return False
        plugin, action = parts
        plugin_caps = self.capabilities.get(plugin, {})
        cap = plugin_caps.get(action)
        if cap is None:
            return False
        return cap.enabled

    def confidence_threshold_for(self, capability_key: str) -> float:
        """Returns the configured threshold, or 1.0 (strictest) if not found."""
        parts = capability_key.split(".", 1)
        if len(parts) != 2:
            return 1.0
        plugin, action = parts
        plugin_caps = self.capabilities.get(plugin, {})
        cap = plugin_caps.get(action)
        if cap is None:
            return 1.0
        return cap.confidence_threshold

    def touches_protected_path(self, action) -> bool:
        """
        Check whether the action's target or payload references a protected path.
        Simple prefix/glob match against the protected_paths list.
        """
        import fnmatch
        payload_paths = []
        if isinstance(action.payload, dict):
            # Collect any string values in payload that look like paths
            for v in action.payload.values():
                if isinstance(v, str) and ("/" in v or v.endswith(".yaml")):
                    payload_paths.append(v)

        for protected in self.protected_paths:
            for path in payload_paths:
                if fnmatch.fnmatch(path, protected):
                    return True
        return False

    @classmethod
    def load(cls, path: str, rate_limiter: RateLimiter) -> "MaintainerConfig":
        """
        Load and validate .ai-maintainer.yaml.
        Raises ValueError on malformed config so startup fails closed.
        """
        with open(path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            raise ValueError(f"Config file {path} is empty or invalid YAML")

        kill_switch = bool(raw.get("kill_switch", False))
        protected_paths = raw.get("protected_paths", [])

        capabilities: dict[str, dict[str, CapabilityConfig]] = {}
        for plugin_name, plugin_actions in raw.get("capabilities", {}).items():
            if not isinstance(plugin_actions, dict):
                raise ValueError(f"Malformed capabilities entry for plugin '{plugin_name}'")
            capabilities[plugin_name] = {}
            for action_name, action_cfg in plugin_actions.items():
                if not isinstance(action_cfg, dict):
                    raise ValueError(
                        f"Malformed action config for '{plugin_name}.{action_name}'"
                    )
                capabilities[plugin_name][action_name] = CapabilityConfig(
                    enabled=bool(action_cfg.get("enabled", False)),
                    confidence_threshold=float(action_cfg.get("confidence_threshold", 1.0)),
                    rate_limit_per_hour=int(
                        action_cfg.get("rate_limit", {}).get("per_hour", 60)
                        if isinstance(action_cfg.get("rate_limit"), dict)
                        else 60
                    ),
                )

        return cls(
            kill_switch_active=kill_switch,
            capabilities=capabilities,
            protected_paths=protected_paths,
            rate_limiter=rate_limiter,
        )
