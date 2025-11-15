import argparse
import configparser
import os
import sys
from dataclasses import dataclass
from typing import Optional
from urllib import request, error
import json


class ConfigError(Exception):
    """Базовая ошибка конфигурации."""


class DependencyFetchError(Exception):
    """Ошибка при получении зависимостей пакета."""


@dataclass
class AppConfig:
    package_name: str
    version: str
    mode: str  # "real" или "test"
    repo_url: Optional[str]
    test_repo_path: Optional[str]
    ascii_tree: bool


def parse_bool(value: str) -> bool:
    true_values = {"1", "true", "yes", "on"}
    false_values = {"0", "false", "no", "off"}

    v = value.strip().lower()
    if v in true_values:
        return True
    if v in false_values:
        return False
    raise ConfigError(
        f"Некорректное булево значение: {value!r}. "
        f"Ожидалось одно из: {', '.join(sorted(true_values | false_values))}"
    )


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
    repo_url: Optional[str] = None
    test_repo_path: Optional[str] = None

    if mode == "real":
        repo_url = section.get("repo_url", "").strip()
        if not repo_url:
            raise ConfigError(
                "Режим 'real': параметр 'repo_url' обязателен и не может быть пустым."
            )
        # Простая проверка: URL должен начинаться с http
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
    data = {
        "package_name": config.package_name,
        "version": config.version,
        "mode": config.mode,
        "repo_url": config.repo_url or "",
        "test_repo_path": config.test_repo_path or "",
        "ascii_tree": str(config.ascii_tree).lower(),
    }

    for key, value in data.items():
        print(f"{key} = {value}")


def build_metadata_url(config: AppConfig) -> str:
    """
    Для формата pip используем JSON API PyPI-подобного репозитория.
    Ожидаем, что repo_url указывает на базовый URL API или корень,
    к которому можно добавить /<name>/<version>/json.
    """
    if config.mode != "real":
        raise DependencyFetchError(
            "Получение данных о зависимостях поддерживается только в режиме 'real' на Этапе 2."
        )

    base = (config.repo_url or "").rstrip("/")
    # типичный случай: https://pypi.org/pypi
    return f"{base}/{config.package_name}/{config.version}/json"


def fetch_metadata_json(url: str) -> dict:
    try:
        with request.urlopen(url) as resp:
            if resp.status != 200:
                raise DependencyFetchError(
                    f"Сервер вернул статус {resp.status} при запросе {url!r}"
                )
            content_type = resp.headers.get("Content-Type", "")
            # Проверка типа содержимого не критична, но полезна
            data = resp.read()
    except error.HTTPError as e:
        raise DependencyFetchError(
            f"HTTP ошибка при запросе {url!r}: {e.code} {e.reason}"
        ) from e
    except error.URLError as e:
        raise DependencyFetchError(
            f"Ошибка сети при запросе {url!r}: {e.reason}"
        ) from e
    except Exception as e:
        raise DependencyFetchError(
            f"Неожиданная ошибка при запросе {url!r}: {e}"
        ) from e

    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise DependencyFetchError(
            f"Не удалось разобрать JSON-ответ от {url!r}: {e}"
        ) from e


def parse_direct_dependencies(metadata: dict) -> list[str]:
    """
    Извлекает прямые зависимости из JSON-ответа PyPI.
    Ориентируемся на поле info.requires_dist (PEP 345 / 566).
    """
    info = metadata.get("info")
    if not isinstance(info, dict):
        raise DependencyFetchError("Неверный формат метаданных: отсутствует объект 'info'.")

    requires = info.get("requires_dist")
    if requires is None:
        # метаданные без указания зависимостей
        return []

    if not isinstance(requires, list):
        raise DependencyFetchError(
            "Неверный формат метаданных: 'requires_dist' должен быть списком."
        )

    dependencies: list[str] = []
    for item in requires:
        if not isinstance(item, str):
            continue

        # Примеры строк:
        # "urllib3 (<3,>=1.21.1)"
        # "socks ; extra == 'socks'"
        # "idna (<4,>=2.5)"
        base_part = item.split(";", 1)[0].strip()
        if not base_part:
            continue
        dependencies.append(base_part)

    return dependencies


def print_direct_dependencies(config: AppConfig) -> None:
    url = build_metadata_url(config)
    metadata = fetch_metadata_json(url)
    deps = parse_direct_dependencies(metadata)

    print()  # пустая строка для читаемости
    print(f"Прямые зависимости пакета {config.package_name}=={config.version}:")
    if not deps:
        print("  (нет прямых зависимостей)")
        return

    for dep in deps:
        print(f"  - {dep}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Инструмент визуализации графа зависимостей.\n"
            "Этап 1: загрузка и валидация конфигурации.\n"
            "Этап 2: получение прямых зависимостей пакета."
        )
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.ini",
        help="Путь к INI файлу конфигурации (по умолчанию: config.ini)",
    )
    parser.add_argument(
        "--no-config-print",
        action="store_true",
        help="Не выводить параметры конфигурации (для этапа 2 можно отключить).",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Необработанная ошибка при чтении конфигурации: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.no_config_print:
        print_config(config)

    # Этап 2: получение прямых зависимостей и вывод на экран
    try:
        print_direct_dependencies(config)
    except DependencyFetchError as e:
        print(f"Ошибка получения зависимостей: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Необработанная ошибка при получении зависимостей: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
