import { HttpResponse } from "@/testing/msw";

import authExternalTokens from "../fixtures/auth-external-tokens.json";
import authLogin from "../fixtures/auth-login.json";
import authMe from "../fixtures/auth-me.json";
import authRegister from "../fixtures/auth-register.json";
import authWhoamiV2 from "../fixtures/auth-whoami-v2.json";
import repoCommitCreated from "../fixtures/repo-commit-created.json";
import repoCommitsHfPage1 from "../fixtures/repo-commits-hf-page-1.json";
import repoCommitsHfPage2 from "../fixtures/repo-commits-hf-page-2.json";
import repoCreate from "../fixtures/repo-create.json";
import repoCreateWithoutId from "../fixtures/repo-create-without-id.json";
import repoInfo from "../fixtures/repo-info.json";
import repoLikeStatus from "../fixtures/repo-like-status.json";
import repoLikers from "../fixtures/repo-likers.json";
import repoPathsInfo from "../fixtures/repo-paths-info.json";
import repoPreuploadRegular from "../fixtures/repo-preupload-regular.json";
import repoRevision from "../fixtures/repo-revision.json";
import repoStats from "../fixtures/repo-stats.json";
import repoTree from "../fixtures/repo-tree.json";
import userLikedRepos from "../fixtures/user-liked-repos.json";
import userOrgs from "../fixtures/user-orgs.json";
import userOverview from "../fixtures/user-overview.json";

export const uiApiFixtures = {
  auth: {
    externalTokens: authExternalTokens,
    login: authLogin,
    me: authMe,
    register: authRegister,
    whoamiV2: authWhoamiV2,
  },
  repo: {
    commitCreated: repoCommitCreated,
    commitsHf: {
      page1: repoCommitsHfPage1,
      page2: repoCommitsHfPage2,
      nextLink:
        "https://hub.local/api/models/alice/demo/commits/main?after=cursor-2",
    },
    create: repoCreate,
    createWithoutId: repoCreateWithoutId,
    info: repoInfo,
    likeStatus: repoLikeStatus,
    likersHf: repoLikers,
    pathsInfo: repoPathsInfo,
    preuploadRegular: repoPreuploadRegular,
    revisionHf: repoRevision,
    stats: repoStats,
    tree: repoTree,
    userLikedReposHf: userLikedRepos,
  },
  organizations: {
    userOrgs,
  },
  userOverview,
};

export function cloneFixture(value) {
  return structuredClone(value);
}

export function jsonResponse(body, init) {
  return HttpResponse.json(cloneFixture(body), init);
}

export async function readJsonBody(request) {
  return request.clone().json();
}

export async function readNdjsonBody(request) {
  const text = await request.clone().text();
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}
