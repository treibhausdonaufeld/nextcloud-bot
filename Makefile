# Define variables
LOCALES_DIR=locales
DOMAIN=messages

# Specify the files or patterns to search for translatable strings
FILES=$(shell find . -name "*.py" -not -path "./.venv/*" -not -path "./.*")

# Default target
all: update_po

# Target to generate .pot file
.pot:
	@pygettext3.py -d $(DOMAIN) -o $(LOCALES_DIR)/$(DOMAIN).pot $(FILES)

# Target to update .po files in each language directory
update_po: .pot
	@for lang in `ls $(LOCALES_DIR)`; do \
		if [ -d $(LOCALES_DIR)/$$lang/LC_MESSAGES ]; then \
			msgmerge --update $(LOCALES_DIR)/$$lang/LC_MESSAGES/$(DOMAIN).po $(LOCALES_DIR)/$(DOMAIN).pot; \
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
