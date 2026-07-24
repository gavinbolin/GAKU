// validate_episode_json
// Pure validation against ../../../schema/episode.schema.json using Ajv.
// No external service calls.

import Ajv, { type ErrorObject } from "ajv";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// TODO(integration): when the build pipeline copies schema next to dist/, swap this path.
const SCHEMA_PATH = resolve(
  __dirname,
  "../../../schema/episode.schema.json"
);

let cachedValidate: ReturnType<Ajv["compile"]> | null = null;

function getValidator() {
  if (cachedValidate) return cachedValidate;
  const ajv = new Ajv({ allErrors: true, strict: false });
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf-8"));
  cachedValidate = ajv.compile(schema);
  return cachedValidate;
}

export interface ValidateResult {
  valid: boolean;
  errors?: ErrorObject[] | null;
}

export async function validateEpisodeJson(
  episode: object
): Promise<ValidateResult> {
  const validate = getValidator();
  const valid = validate(episode);
  return {
    valid: !!valid,
    errors: valid ? null : validate.errors,
  };
}
