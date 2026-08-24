function unavailable() {
  throw new Error("Standalone AI SDK execution is not included; delegate semantic work to the WorkBuddy Memory Sub-agent");
}

export const generateText = unavailable;
export const jsonSchema = unavailable;
export const stepCountIs = unavailable;
export const tool = unavailable;
