# Define variables
LOCALES_DIR=locales
DOMAIN=messages

# Default target
all: update_po

# Target to generate .pot file (python sources + Jinja2 templates, see babel.cfg)
.pot:
	@uv run pybabel extract -F babel.cfg --no-location -o $(LOCALES_DIR)/$(DOMAIN).pot .

# Target to update .po files in each language directory
update_po: .pot
	@for lang in `ls $(LOCALES_DIR)`; do \
		if [ -d $(LOCALES_DIR)/$$lang/LC_MESSAGES ]; then \
			msgmerge --no-fuzzy-matching --update $(LOCALES_DIR)/$$lang/LC_MESSAGES/$(DOMAIN).po $(LOCALES_DIR)/$(DOMAIN).pot; \
		fi \
	done

compile:
	@for lang in `ls $(LOCALES_DIR)`; do \
		if [ -d $(LOCALES_DIR)/$$lang/LC_MESSAGES ]; then \
			msgfmt $(LOCALES_DIR)/$$lang/LC_MESSAGES/$(DOMAIN).po -o $(LOCALES_DIR)/$$lang/LC_MESSAGES/$(DOMAIN).mo; \
		fi \
	done

# Phony targets
.PHONY: all update_po
