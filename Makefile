PY = .venv/bin/python

.PHONY: site pdf search serve clean new deps

deps:                 ## venv + зависимости; чинит и полусозданное окружение
	@test -x .venv/bin/python || python3 -m venv .venv
	@.venv/bin/python -c "import yaml" 2>/dev/null || .venv/bin/pip install --quiet pyyaml

site: deps            ## собрать сайт в site/
	$(PY) build.py

pdf: deps             ## собрать сайт + все PDF
	$(PY) build.py --pdf

search: site          ## добавить поисковый индекс (нужен node)
	npx --yes pagefind --site site

serve:                ## локальный предпросмотр на http://localhost:8000
	python3 -m http.server 8000 -d site

watch: deps           ## живой предпросмотр на http://localhost:8765 (порт: make watch PORT=...)
	$(PY) dev.py

clean:
	rm -rf site .cache

new:                  ## новая статья: make new DEST=razdel/podtema/slug
	@test -n "$(DEST)" || (echo "Использование: make new DEST=razdel/podtema/slug"; exit 1)
	@test ! -e "content/$(DEST)" || (echo "content/$(DEST) уже существует"; exit 1)
	mkdir -p "content/$(dir $(DEST))"
	cp -r _template/article "content/$(DEST)"
	mkdir -p "content/$(DEST)/figures"
	@echo "Создано: content/$(DEST) — заполните meta.yml и блоки"
