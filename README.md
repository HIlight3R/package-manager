# Package manager
*Вариант 25*

## Запуск
```bash
python main.py
# или python main.py --config my_config.ini
```

## Структура `config.ini`
```ini
[app]
package_name = requests
version = 2.31.0

# Режим работы:
# real  – работа с реальным репозиторием (URL)
# test  – работа с тестовым репозиторием (файл)
mode = real

# Если mode = real, используется repo_url
repo_url = https://pypi.org/simple/

# Если mode = test, используется test_repo_path
test_repo_path = ./test_repo.txt

# Режим ASCII-дерева:
# true/false, yes/no, 1/0
ascii_tree = true
```

## Этапы работы
### Этап 1
Создано минимальное настраиваемое CLI-приложение.