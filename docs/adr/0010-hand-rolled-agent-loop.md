# Hand-rolled agent loop, no LangChain or LangGraph

The Mood search agent is a plain loop behind a provider-agnostic model interface, with a step cap and grounding checked in code. A framework was rejected because the project's purpose is to demonstrate and learn how agents work, and because a small explicit loop is easier to trace in Langfuse, test with recorded outputs and swap providers under.
