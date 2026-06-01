SHELL := /bin/bash
BUILD := build
SAMPLE := sample_game.py

.PHONY: all clean

all: $(BUILD)/scorecard.pdf

$(BUILD)/scorecard.pdf: $(SAMPLE) templates/o27_scorecard.mp templates/final_scorecard.tex
	@mkdir -p $(BUILD)
	cp templates/o27_scorecard.mp $(BUILD)/team_scorecard_template.mp
	cp templates/final_scorecard.tex $(BUILD)/final_scorecard_template.tex
	cd $(BUILD) && python3 ../$(SAMPLE)
	cd $(BUILD) && mpost -tex=latex scorecard_away.mp
	cd $(BUILD) && mpost -tex=latex scorecard_home.mp
	cd $(BUILD) && mptopdf scorecard_away.0 && mptopdf scorecard_home.0
	cd $(BUILD) && pdflatex -interaction=nonstopmode scorecard.tex >/dev/null

clean:
	rm -rf $(BUILD)
