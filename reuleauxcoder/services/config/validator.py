"""Configuration validator."""

from reuleauxcoder.domain.config.models import Config


class ConfigValidator:
    """Validates configuration."""

    def validate(self, config: Config) -> list[str]:
        """Validate configuration and return list of errors."""
        return config.validate()

    def is_valid(self, config: Config) -> bool:
        """Check if configuration is valid."""
        return config.is_valid()

    def validate_or_raise(self, config: Config) -> None:
        """Validate configuration and raise if invalid."""
        errors = self.validate(config)
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
