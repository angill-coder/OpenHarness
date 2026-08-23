export class Agent {
  constructor() {
    throw new Error("TCVDB HTTP transport is not included in the local SQLite distribution");
  }
}

export async function request() {
  throw new Error("TCVDB HTTP transport is not included in the local SQLite distribution");
}
