from __future__ import annotations

from services.flow_relative_value_lab import write_relative_value_artifact


if __name__ == "__main__":
    print(write_relative_value_artifact().as_posix())
