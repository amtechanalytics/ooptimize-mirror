public class Orig {
  protected static int hybridUnsignedBinarySearch(
      final char[] array, final int begin, final int end, final char k) {
    // next line accelerates the possibly common case where the value would
    // be inserted at the end
    if ((end > 0) && ((array[end - 1]) < (int) k)) {
      return -end - 1;
    }
    int low = begin;
    int high = end - 1;
    // 32 in the next line matches the size of a cache line
    while (low + 32 <= high) {
      final int middleIndex = (low + high) >>> 1;
      final int middleValue = (array[middleIndex]);

      if (middleValue < (int) k) {
        low = middleIndex + 1;
      } else if (middleValue > (int) k) {
        high = middleIndex - 1;
      } else {
        return middleIndex;
      }
    }
    // we finish the job with a sequential search
    int x = low;
    for (; x <= high; ++x) {
      final int val = (array[x]);
      if (val >= (int) k) {
        if (val == (int) k) {
          return x;
        }
        break;
      }
    }
    return -(x + 1);
  }
  public static int advanceUntil(char[] array, int pos, int length, char min) {
    int lower = pos + 1;

    // special handling for a possibly common sequential case
    if (lower >= length || (array[lower]) >= (int) (min)) {
      return lower;
    }

    int spansize = 1; // could set larger
    // bootstrap an upper limit

    while (lower + spansize < length && (array[lower + spansize]) < (int) (min)) {
      spansize *= 2; // hoping for compiler will reduce to
    }
    // shift
    int upper = (lower + spansize < length) ? lower + spansize : length - 1;

    // maybe we are lucky (could be common case when the seek ahead
    // expected
    // to be small and sequential will otherwise make us look bad)
    if (array[upper] == min) {
      return upper;
    }

    if ((array[upper]) < (int) (min)) {
      // means array has no item >= min pos = array.length;
      return length;
    }

    // we know that the next-smallest span was too small
    lower += (spansize >>> 1);

    // else begin binary search
    // invariant: array[lower]<min && array[upper]>min
    while (lower + 1 != upper) {
      int mid = (lower + upper) >>> 1;
      char arraymid = array[mid];
      if (arraymid == min) {
        return mid;
      } else if ((arraymid) < (int) (min)) {
        lower = mid;
      } else {
        upper = mid;
      }
    }
    return upper;
  }
  protected static int unsignedOneSidedGallopingIntersect2by2(
      final char[] smallSet,
      final int smallLength,
      final char[] largeSet,
      final int largeLength,
      final char[] buffer) {
    if (0 == smallLength) {
      return 0;
    }
    int k1 = 0;
    int k2 = 0;
    int pos = 0;
    char s1 = largeSet[k1];
    char s2 = smallSet[k2];
    while (true) {
      if (s1 < s2) {
        k1 = advanceUntil(largeSet, k1, largeLength, s2);
        if (k1 == largeLength) {
          break;
        }
        s1 = largeSet[k1];
      }
      if (s2 < s1) {
        ++k2;
        if (k2 == smallLength) {
          break;
        }
        s2 = smallSet[k2];
      } else {
        // (set2[k2] == set1[k1])
        buffer[pos++] = s2;
        ++k2;
        if (k2 == smallLength) {
          break;
        }
        s2 = smallSet[k2];
        k1 = advanceUntil(largeSet, k1, largeLength, s2);
        if (k1 == largeLength) {
          break;
        }
        s1 = largeSet[k1];
      }
    }
    return pos;
  }
  public static int cardinalityInBitmapRange(long[] bitmap, int start, int end) {
    if (start >= end) {
      return 0;
    }
    int firstword = start / 64;
    int endword = (end - 1) / 64;
    if (firstword == endword) {
      return Long.bitCount(bitmap[firstword] & ((~0L << start) & (~0L >>> -end)));
    }
    int answer = Long.bitCount(bitmap[firstword] & (~0L << start));
    for (int i = firstword + 1; i < endword; i++) {
      answer += Long.bitCount(bitmap[i]);
    }
    answer += Long.bitCount(bitmap[endword] & (~0L >>> -end));
    return answer;
  }
  protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left, j = i; i < right; j = ++i) {
      byte ai = key[i + 1];
      Object child = children[i + 1];
      int unsignedByteAi = Byte.toUnsignedInt(ai);
      while (unsignedByteAi < Byte.toUnsignedInt(key[j])) {
        key[j + 1] = key[j];
        children[j + 1] = children[j];
        if (j-- == left) {
          break;
        }
      }
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
