import {
  GetObjectCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const IMAGE_EXT = /\.(jpe?g|png|gif|webp)$/i;

type S3Config = {
  region: string;
  bucket: string;
  listPrefix: string;
  uploadPrefix: string;
};

let cachedClient: S3Client | null = null;

function normalizePrefix(prefix: string): string {
  if (!prefix.trim()) return "";
  const p = prefix.replace(/^\/+/, "");
  return p.endsWith("/") ? p : `${p}/`;
}

function getConfig(): S3Config {
  const region = (import.meta.env.VITE_AWS_REGION as string | undefined)?.trim();
  const bucket = (import.meta.env.VITE_S3_BUCKET as string | undefined)?.trim();
  const listPrefix = normalizePrefix(
    (import.meta.env.VITE_S3_LIST_PREFIX as string | undefined) ?? ""
  );
  const uploadPrefix = normalizePrefix(
    (import.meta.env.VITE_S3_UPLOAD_PREFIX as string | undefined) ?? ""
  );

  if (!region) throw new Error("Missing VITE_AWS_REGION");
  if (!bucket) throw new Error("Missing VITE_S3_BUCKET");
  return { region, bucket, listPrefix, uploadPrefix };
}

function getClient(region: string): S3Client {
  if (cachedClient) return cachedClient;
  const accessKeyId = (
    import.meta.env.VITE_AWS_ACCESS_KEY_ID as string | undefined
  )?.trim();
  const secretAccessKey = (
    import.meta.env.VITE_AWS_SECRET_ACCESS_KEY as string | undefined
  )?.trim();
  const sessionToken = (
    import.meta.env.VITE_AWS_SESSION_TOKEN as string | undefined
  )?.trim();

  if (!accessKeyId || !secretAccessKey) {
    throw new Error(
      "Missing frontend AWS credentials: VITE_AWS_ACCESS_KEY_ID / VITE_AWS_SECRET_ACCESS_KEY"
    );
  }

  cachedClient = new S3Client({
    region,
    credentials: {
      accessKeyId,
      secretAccessKey,
      sessionToken: sessionToken || undefined,
    },
  });
  return cachedClient;
}

async function listImageKeys(prefix: string): Promise<string[]> {
  const { region, bucket } = getConfig();
  const client = getClient(region);
  const keys: string[] = [];

  let token: string | undefined;
  do {
    const page = await client.send(
      new ListObjectsV2Command({
        Bucket: bucket,
        Prefix: prefix,
        ContinuationToken: token,
        MaxKeys: 500,
      })
    );
    for (const obj of page.Contents ?? []) {
      if (obj.Key && !obj.Key.endsWith("/") && IMAGE_EXT.test(obj.Key)) {
        keys.push(obj.Key);
      }
    }
    token = page.IsTruncated ? page.NextContinuationToken : undefined;
  } while (token && keys.length < 2000);

  return keys;
}

export type S3ImageChoice = {
  key: string;
  url: string;
};

export async function getAllS3ImageChoices(): Promise<{
  choices: S3ImageChoice[];
  notice?: string;
}> {
  const { listPrefix, uploadPrefix } = getConfig();
  const primaryPrefix = uploadPrefix || listPrefix;
  const keys = await listImageKeys(primaryPrefix);
  if (!keys.length) {
    return {
      choices: [],
      notice: primaryPrefix
        ? `No images found in "${primaryPrefix}".`
        : "No image objects found in this bucket.",
    };
  }

  const choices = await Promise.all(
    keys.map(async (key) => ({
      key,
      url: await getSignedImageUrl(key),
    }))
  );

  return {
    choices,
    notice: primaryPrefix
      ? `Showing ${keys.length} image(s) from "${primaryPrefix}".`
      : undefined,
  };
}

export async function getSignedImageUrl(key: string): Promise<string> {
  const { region, bucket } = getConfig();
  const client = getClient(region);
  return getSignedUrl(
    client,
    new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    }),
    { expiresIn: 60 * 60 }
  );
}

export function buildUploadKey(): string {
  const { uploadPrefix } = getConfig();
  const stamp = Date.now();
  const rand = Math.random().toString(36).slice(2, 10);
  return `${uploadPrefix}${stamp}-${rand}.jpg`;
}

export async function uploadJpegToS3(blob: Blob, key?: string): Promise<string> {
  const { region, bucket } = getConfig();
  const client = getClient(region);
  const objectKey = key || buildUploadKey();
  const bytes = new Uint8Array(await blob.arrayBuffer());
  await client.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: objectKey,
      Body: bytes,
      ContentLength: bytes.byteLength,
      ContentType: "image/jpeg",
    })
  );
  return objectKey;
}
