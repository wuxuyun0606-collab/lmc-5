.PHONY: test demo doctor

test:
	PYTHONPATH=src python3 -m pytest tests

demo:
	PYTHONPATH=src python3 examples/demo.py

doctor:
	PYTHONPATH=src python3 -m lmc5 doctor --db /tmp/lmc5-doctor.sqlite
