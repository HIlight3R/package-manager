import argparse
import configparser
import os
import sys
from dataclasses import dataclass


class ConfigError(Exception):
    """Базовая ошибка конфигурации."""


@dataclass
class AppConfig:
    package_name: str
    version: str
    mode: str  # "real" или "test"
    repo_url: str | None
    test_repo_path: str | None
    ascii_tree: bool


def parse_bool(value: str) -> bool:
    true_values = {"1", "true", "yes", "on"}
    false_values = {"0", "false", "no", "off"}

    v = value.strip().lower()
    if v in true_values:
        return True
    if v in false_values:
        return False
    raise ConfigError(f"Некорректное булево значение: {value!r}. "
                      f"Ожидалось одно из: {', '.join(sorted(true_values | false_values))}")


def validate_version(version: str) -> None:
    # Простая проверка вида X.Y[.Z] — можно усложнить при желании
    parts = version.split(".")
    if not 2 <= len(parts) <= 3:
        raise ConfigError(
            f"Некорректная версия пакета: {version!r}. "
            "Ожидался формат X.Y или X.Y.Z"
        )
    for p in parts:
        if not p.isdigit():
            raise ConfigError(
                f"Некорректная версия пакета: {version!r}. "
                "Части версии должны быть числами."
            )


def load_config(path: str) -> AppConfig:
    if not os.path.exists(path):
        raise ConfigError(f"Файл конфигурации не найден: {path}")

    parser = configparser.ConfigParser()
    try:
        read_files = parser.read(path, encoding="utf-8")
    except configparser.Error as e:
        raise ConfigError(f"Ошибка чтения INI файла: {e}") from e

    if not read_files:
        raise ConfigError(f"Не удалось прочитать файл конфигурации: {path}")

    if "app" not in parser:
        raise ConfigError("Секция [app] отсутствует в файле конфигурации.")

    section = parser["app"]

    # 1. Имя пакета
    package_name = section.get("package_name", "").strip()
    if not package_name:
        raise ConfigError("Параметр 'package_name' обязателен и не может быть пустым.")

    # 2. Версия
    version = section.get("version", "").strip()
    if not version:
        raise ConfigError("Параметр 'version' обязателен и не может быть пустым.")
    validate_version(version)

    # 3. Режим работы: real / test
    mode = section.get("mode", "").strip().lower()
    if mode not in {"real", "test"}:
        raise ConfigError(
            f"Некорректный режим работы 'mode': {mode!r}. "
            "Допустимые значения: 'real' или 'test'."
        )

    # 4. URL реального репозитория или путь к тестовому
    repo_url = None
    test_repo_path = None

    if mode == "real":
        repo_url = section.get("repo_url", "").strip()
        if not repo_url:
            raise ConfigError(
                "Режим 'real': параметр 'repo_url' обязателен и не может быть пустым."
            )
        # Тут можно добавить простую проверку вида URL (начинается с http)
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            raise ConfigError(
                f"Некорректный 'repo_url': {repo_url!r}. "
                "Ожидался URL, начинающийся с 'http://' или 'https://'."
            )

    elif mode == "test":
        test_repo_path = section.get("test_repo_path", "").strip()
        if not test_repo_path:
            raise ConfigError(
                "Режим 'test': параметр 'test_repo_path' обязателен и не может быть пустым."
            )
        if not os.path.exists(test_repo_path):
            raise ConfigError(
                f"Режим 'test': файл тестового репозитория не найден: {test_repo_path}"
            )

    # 5. Режим ASCII-дерева
    ascii_tree_raw = section.get("ascii_tree", "false")
    ascii_tree = parse_bool(ascii_tree_raw)

    return AppConfig(
        package_name=package_name,
        version=version,
        mode=mode,
        repo_url=repo_url,
        test_repo_path=test_repo_path,
        ascii_tree=ascii_tree,
    )


def print_config(config: AppConfig) -> None:
    """
    Вывод всех параметров в формате ключ = значение.
    Для удобства выводим "плоский" список без секций.
    """
    data: dict[str, str] = {
        "package_name": config.package_name,
        "version": config.version,
        "mode": config.mode,
        "repo_url": config.repo_url or "",
        "test_repo_path": config.test_repo_path or "",
        "ascii_tree": str(config.ascii_tree).lower(),
    }

    for key, value in data.items():
        print(f"{key} = {value}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей (Этап 1: конфигурация)."
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.ini",
        help="Путь к INI файлу конфигурации (по умолчанию: config.ini)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # На всякий случай общий catch, чтобы показать неожиданные ошибки
        print(f"Необработанная ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    # Для Этапа 1 просто выводим все параметры в формате ключ = значение
    print_config(config)


if __name__ == "__main__":
    main()
