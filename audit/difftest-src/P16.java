public class P16 {
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
    int pos = 0;
    char s1 = largeSet[k1];
    for (int k2 = 0; k2 < smallLength; ++k2) {
      char s2 = smallSet[k2];
      if (s1 < s2) {
        k1 = advanceUntil(largeSet, k1, largeLength, s2);
        if (k1 == largeLength) {
          break;
        }
        s1 = largeSet[k1];
      }
      if (s1 == s2) {
        buffer[pos++] = s2;
        if (k2 + 1 == smallLength || ++k1 == largeLength) {
          break;
        }
        s1 = largeSet[k1];
      }
    }
    return pos;
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
}
