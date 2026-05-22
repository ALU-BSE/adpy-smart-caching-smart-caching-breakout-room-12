# blog/views.py

import time
import logging

from django.core.cache import cacheclear
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status

from .models import Post
from .serializers import PostSerializer

logger = logging.getLogger(__name__)

# CACHE HELPERS (Level 4  Invalidation)

POSTS_LIST_KEYS_SET = "posts:list:keys"


def register_posts_list_cache_key(key: str) -> None:
    """
    Track list cache keys so we can invalidate them all on mutations.
    """
    keys = cache.get(POSTS_LIST_KEYS_SET, set())
    if not isinstance(keys, set):
        keys = set(keys)
    keys.add(key)
    cache.set(POSTS_LIST_KEYS_SET, keys, timeout=None)


def invalidate_posts_list_cache() -> None:
    """
    Delete all tracked list cache keys when posts change.
    """
    keys = cache.get(POSTS_LIST_KEYS_SET, set())
    if not keys:
        return
    for k in keys:
        cache.delete(k)
    cache.delete(POSTS_LIST_KEYS_SET)


def invalidate_post_caches(post: Post) -> None:
    """
    Central place to invalidate caches related to a single post.
    """

    invalidate_posts_list_cache()

    # Invalidate detail cache for this post
    cache.delete(f"posts:detail:{post.id}")

    # Invalidate author's drafts cache (in case status/drafts changed)
    cache.delete(f"my-drafts:{post.author_id}")



# LEVEL 2 — Shared Cache (Public Data)

class PostListView(APIView):
    """
    GET  /api/posts/
    POST /api/posts/
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request):
        params = request.query_params.urlencode()
        cache_key = f"posts:list:{params}"

        cached = cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT: posts list")
            return Response(cached)

        logger.info("Cache MISS: posts list")

        posts = (
            Post.objects.filter(status=Post.STATUS_PUBLISHED)
            .select_related("author")
        )

        serializer = PostSerializer(posts, many=True)

        cache.set(cache_key, serializer.data, timeout=300)
        register_posts_list_cache_key(cache_key)

        return Response(serializer.data)

    def post(self, request):
        serializer = PostSerializer(data=request.data)

        if serializer.is_valid():
            post = serializer.save(author=request.user)

            # LEVEL 4 — Invalidation on mutation
            invalidate_post_caches(post)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



# LEVEL 2 — Single Post Cache

class PostDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, post_id: int):
        cache_key = f"posts:detail:{post_id}"

        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"Cache HIT: post {post_id}")
            return Response(cached)

        logger.info(f"Cache MISS: post {post_id}")

        try:
            post = (
                Post.objects.select_related("author")
                .get(id=post_id, status=Post.STATUS_PUBLISHED)
            )
        except Post.DoesNotExist:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PostSerializer(post)

        cache.set(cache_key, serializer.data, timeout=600)

        return Response(serializer.data)



# LEVEL 3 — User-Isolated Cache (FIXED)


class MyDraftsView(APIView):
    """
    GET /api/posts/my-drafts/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # LEVEL 3: USER-ISOLATED CACHE
        #
        # SECURITY NOTE:
        # If we used a shared key like "my-drafts",
        # User A could see User B's cached drafts (data leak).
      

        cache_key = f"my-drafts:{user.id}"

        cached_data = cache.get(cache_key)
        if cached_data is not None:
            logger.info("Cache HIT: my drafts")
            return Response(cached_data)

        logger.info("Cache MISS: my drafts")

        drafts = (
            Post.objects.filter(
                author=user,
                status=Post.STATUS_DRAFT,
            )
            .select_related("author")
        )

        serializer = PostSerializer(drafts, many=True)

        cache.set(cache_key, serializer.data, timeout=120)

        return Response(serializer.data)


# ---------------------------------------------------------------------------
# BONUS — Deliberately Broken View (DO NOT FIX)
# ---------------------------------------------------------------------------

class BrokenDraftsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = cache.get("my-drafts")

        if data is None:
            drafts = (
                Post.objects.filter(
                    author=request.user,
                    status=Post.STATUS_DRAFT,
                )
                .select_related("author")
            )

            serializer = PostSerializer(drafts, many=True)

            data = serializer.data

            cache.set("my-drafts", data, timeout=120)

        return Response(data)