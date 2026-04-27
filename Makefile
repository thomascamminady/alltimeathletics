.PHONY: help sync lint typecheck test scrape site serve all clean nuke ci-local trigger-update trigger-deploy watch

PORT ?= 8766

help:
	@echo "alltimeathletics — local commands"
	@echo
	@echo "  sync           Install/refresh dependencies via uv"
	@echo "  lint           Run ruff against src + tests"
	@echo "  typecheck      Run ty against src"
	@echo "  test           Run pytest (parser + schema)"
	@echo "  ci-local       lint + typecheck + test (mirrors .github/workflows/ci.yml)"
	@echo "  scrape         Run the pipeline (uses .cache/, ~2 min cold, secs warm)"
	@echo "  site           Render the static site into ./site/"
	@echo "  serve          Serve ./site/ at http://localhost:$(PORT)"
	@echo "  all            sync + ci-local + scrape + site"
	@echo "  clean          Remove site/ and data/events/"
	@echo "  nuke           clean + drop .cache/ (forces full re-scrape)"
	@echo
	@echo "GitHub automation (require gh auth):"
	@echo "  trigger-update Run the weekly data refresh workflow now"
	@echo "  trigger-deploy Run the Pages deploy workflow now"
	@echo "  watch          Tail the most recent workflow run"

sync:
	uv sync

lint:
	uv run ruff check src tests

typecheck:
	uv run ty check src

test:
	uv run pytest

ci-local: lint typecheck test

scrape:
	uv run python -m alltimeathletics.pipeline --cache_dir .cache

site:
	uv run python -m alltimeathletics.site --out site/

serve:
	@echo "Serving site/ at http://localhost:$(PORT)/  (Ctrl-C to stop)"
	python3 -m http.server $(PORT) --directory site

all: sync ci-local scrape site
	@echo
	@echo "Done. Run 'make serve' to preview, or 'make site && make serve'."

clean:
	rm -rf site data/events

nuke: clean
	rm -rf .cache

trigger-update:
	gh workflow run update-data.yml

trigger-deploy:
	gh workflow run deploy.yml

watch:
	gh run watch
