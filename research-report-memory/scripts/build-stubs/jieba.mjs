function tokenize(text) {
  return String(text).match(/[\p{Script=Han}]|[\p{Letter}\p{Number}_]+/gu) ?? [];
}

export class Jieba {
  static withDict() {
    return new Jieba();
  }

  cut(text) {
    return tokenize(text);
  }

  cutAll(text) {
    return tokenize(text);
  }

  cutForSearch(text) {
    return tokenize(text);
  }

  loadDict() {}
}
