.PHONY: test build run run-e2e clean

# Default target
all: build test

# Install dependencies using uv
build:
	uv sync

# Run the unit tests via pytest
test:
	uv run python -m pytest tests/test_memmcp.py -v

# Run the MCP Server directly
run:
	uv run python src/server.py

# Run the End-to-End integration tests
run-e2e:
	uv run python tests/e2e_test.py

# Clean up SQLite and FAISS artifacts
clean:
	rm -f memmcp.db memmcp.db-shm memmcp.db-wal memory_wal.log test*.db test*.log
	rm -rf __pycache__ src/__pycache__ tests/__pycache__
