// Обвязка тестов taskgen для C++. Исполняется внутри песочницы, а не в сервисе.
//
// Сервис склеивает prologue.hpp, решение, строку #define TASKGEN_ENTRYPOINT и этот файл
// в одну единицу трансляции. Протокол описан в taskgen/sandbox/protocol.py. Вход кейса —
// JSON-массив аргументов, ответ функции переводится обратно в JSON.
//
// Контракт для решения: свободная функция без перегрузок, main не пишется, стандартные
// заголовки уже подключены. Типы параметров и ответа — те, для которых ниже есть Convert:
// bool, целые, вещественные, char, std::string, std::vector, std::pair, std::map и
// std::unordered_map со строковым ключом, в любой вложенности.

namespace taskgen_harness {

struct Json {
    enum Kind { Null, Bool, Number, String, Array, Object };
    Kind kind = Null;
    bool boolean = false;
    bool integral = false;
    long long integer = 0;
    double number = 0;
    std::string text;
    std::vector<Json> items;
    std::vector<std::pair<std::string, Json>> fields;
};

class Parser {
  public:
    explicit Parser(const std::string& source) : s(source) {}

    Json parse() {
        Json value = parse_value();
        skip_space();
        if (pos != s.size()) fail("лишние символы после JSON");
        return value;
    }

  private:
    const std::string& s;
    size_t pos = 0;

    [[noreturn]] void fail(const std::string& what) {
        throw std::runtime_error("неверный JSON на входе: " + what);
    }

    void skip_space() {
        while (pos < s.size() && std::isspace(static_cast<unsigned char>(s[pos]))) pos++;
    }

    bool take(const char* word) {
        size_t n = std::strlen(word);
        if (s.compare(pos, n, word) != 0) return false;
        pos += n;
        return true;
    }

    Json parse_value() {
        skip_space();
        if (pos >= s.size()) fail("неожиданный конец");
        Json value;
        char c = s[pos];
        if (c == '{') {
            value.kind = Json::Object;
            pos++;
            skip_space();
            if (pos < s.size() && s[pos] == '}') return pos++, value;
            while (true) {
                skip_space();
                if (pos >= s.size() || s[pos] != '"') fail("ожидался ключ");
                std::string key = parse_string();
                skip_space();
                if (pos >= s.size() || s[pos++] != ':') fail("ожидалось ':'");
                value.fields.emplace_back(key, parse_value());
                skip_space();
                if (pos < s.size() && s[pos] == ',') { pos++; continue; }
                if (pos < s.size() && s[pos] == '}') return pos++, value;
                fail("ожидалось ',' или '}'");
            }
        }
        if (c == '[') {
            value.kind = Json::Array;
            pos++;
            skip_space();
            if (pos < s.size() && s[pos] == ']') return pos++, value;
            while (true) {
                value.items.push_back(parse_value());
                skip_space();
                if (pos < s.size() && s[pos] == ',') { pos++; continue; }
                if (pos < s.size() && s[pos] == ']') return pos++, value;
                fail("ожидалось ',' или ']'");
            }
        }
        if (c == '"') {
            value.kind = Json::String;
            value.text = parse_string();
            return value;
        }
        if (take("true")) { value.kind = Json::Bool; value.boolean = true; return value; }
        if (take("false")) { value.kind = Json::Bool; return value; }
        if (take("null")) return value;
        return parse_number();
    }

    Json parse_number() {
        size_t start = pos;
        if (pos < s.size() && s[pos] == '-') pos++;
        bool fractional = false;
        while (pos < s.size()) {
            char c = s[pos];
            if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') fractional = true;
            else if (!std::isdigit(static_cast<unsigned char>(c))) break;
            pos++;
        }
        std::string token = s.substr(start, pos - start);
        if (token.empty() || token == "-") fail("ожидалось значение");
        Json value;
        value.kind = Json::Number;
        value.number = std::strtod(token.c_str(), nullptr);
        if (!fractional) {
            errno = 0;
            value.integer = std::strtoll(token.c_str(), nullptr, 10);
            value.integral = errno != ERANGE;
        }
        return value;
    }

    static void append_utf8(std::string& out, unsigned code) {
        if (code < 0x80) {
            out += static_cast<char>(code);
        } else if (code < 0x800) {
            out += static_cast<char>(0xC0 | (code >> 6));
            out += static_cast<char>(0x80 | (code & 0x3F));
        } else if (code < 0x10000) {
            out += static_cast<char>(0xE0 | (code >> 12));
            out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (code & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (code >> 18));
            out += static_cast<char>(0x80 | ((code >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (code & 0x3F));
        }
    }

    unsigned parse_hex4() {
        if (pos + 4 > s.size()) fail("обрезанная escape-последовательность");
        unsigned code = std::stoul(s.substr(pos, 4), nullptr, 16);
        pos += 4;
        return code;
    }

    std::string parse_string() {
        pos++;
        std::string out;
        while (pos < s.size() && s[pos] != '"') {
            char c = s[pos++];
            if (c != '\\') { out += c; continue; }
            if (pos >= s.size()) fail("обрезанная строка");
            char e = s[pos++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned code = parse_hex4();
                    // Символ вне базовой плоскости приходит суррогатной парой.
                    if (code >= 0xD800 && code < 0xDC00 && take("\\u")) {
                        unsigned low = parse_hex4();
                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    }
                    append_utf8(out, code);
                    break;
                }
                default: fail("неизвестная escape-последовательность");
            }
        }
        if (pos >= s.size()) fail("незакрытая строка");
        pos++;
        return out;
    }
};

[[noreturn]] inline void type_error(const char* expected) {
    throw std::invalid_argument(std::string("аргумент не приводится к типу: ") + expected);
}

template <class T, class = void>
struct Convert;

template <>
struct Convert<bool> {
    static bool from(const Json& j) {
        if (j.kind != Json::Bool) type_error("bool");
        return j.boolean;
    }
    static void dump(std::string& out, bool v) { out += v ? "true" : "false"; }
};

template <class T>
struct Convert<T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>>> {
    static T from(const Json& j) {
        if (j.kind != Json::Number || !j.integral) type_error("целое");
        if (j.integer < static_cast<long long>(std::numeric_limits<T>::min()) ||
            (j.integer > 0 &&
             static_cast<unsigned long long>(j.integer) > std::numeric_limits<T>::max()))
            type_error("целое нужной разрядности");
        return static_cast<T>(j.integer);
    }
    static void dump(std::string& out, T v) { out += std::to_string(v); }
};

template <class T>
struct Convert<T, std::enable_if_t<std::is_floating_point_v<T>>> {
    static T from(const Json& j) {
        if (j.kind != Json::Number) type_error("число");
        return static_cast<T>(j.number);
    }
    static void dump(std::string& out, T v) {
        if (!std::isfinite(v)) throw std::domain_error("ответ не переводится в JSON: не число");
        char buffer[32];
        std::snprintf(buffer, sizeof buffer, "%.17g", static_cast<double>(v));
        out += buffer;
    }
};

inline void dump_string(std::string& out, const std::string& v) {
    out += '"';
    for (unsigned char c : v) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c < 0x20) {
            char buffer[8];
            std::snprintf(buffer, sizeof buffer, "\\u%04x", c);
            out += buffer;
        } else out += static_cast<char>(c);
    }
    out += '"';
}

template <>
struct Convert<std::string> {
    static std::string from(const Json& j) {
        if (j.kind != Json::String) type_error("строка");
        return j.text;
    }
    static void dump(std::string& out, const std::string& v) { dump_string(out, v); }
};

template <>
struct Convert<char> {
    static char from(const Json& j) {
        if (j.kind != Json::String || j.text.size() != 1) type_error("символ");
        return j.text[0];
    }
    static void dump(std::string& out, char v) { dump_string(out, std::string(1, v)); }
};

template <>
struct Convert<const char*> {
    static void dump(std::string& out, const char* v) { dump_string(out, v); }
};

template <class T>
struct Convert<std::vector<T>> {
    static std::vector<T> from(const Json& j) {
        if (j.kind != Json::Array) type_error("массив");
        std::vector<T> v;
        v.reserve(j.items.size());
        for (const Json& item : j.items) v.push_back(Convert<T>::from(item));
        return v;
    }
    static void dump(std::string& out, const std::vector<T>& v) {
        out += '[';
        for (size_t i = 0; i < v.size(); i++) {
            if (i) out += ',';
            Convert<T>::dump(out, v[i]);
        }
        out += ']';
    }
};

template <class A, class B>
struct Convert<std::pair<A, B>> {
    static std::pair<A, B> from(const Json& j) {
        if (j.kind != Json::Array || j.items.size() != 2) type_error("пара");
        return {Convert<A>::from(j.items[0]), Convert<B>::from(j.items[1])};
    }
    static void dump(std::string& out, const std::pair<A, B>& v) {
        out += '[';
        Convert<A>::dump(out, v.first);
        out += ',';
        Convert<B>::dump(out, v.second);
        out += ']';
    }
};

template <class Map, class T>
struct ConvertMap {
    static Map from(const Json& j) {
        if (j.kind != Json::Object) type_error("объект");
        Map m;
        for (const auto& [key, item] : j.fields) m.emplace(key, Convert<T>::from(item));
        return m;
    }
    static void dump(std::string& out, const Map& m) {
        out += '{';
        bool first = true;
        for (const auto& [key, item] : m) {
            if (!first) out += ',';
            first = false;
            dump_string(out, key);
            out += ':';
            Convert<T>::dump(out, item);
        }
        out += '}';
    }
};

template <class T>
struct Convert<std::map<std::string, T>> : ConvertMap<std::map<std::string, T>, T> {};

template <class T>
struct Convert<std::unordered_map<std::string, T>>
    : ConvertMap<std::unordered_map<std::string, T>, T> {};

template <class R, class... A, size_t... I>
std::string call(R (*function)(A...), const Json& args, std::index_sequence<I...>, double& ms) {
    if (args.kind != Json::Array || args.items.size() != sizeof...(A))
        throw std::invalid_argument("вход кейса — не массив из " +
                                    std::to_string(sizeof...(A)) + " аргументов");
    // Значения хранятся в кортеже, чтобы параметр вида T& получил lvalue.
    std::tuple<std::decay_t<A>...> values{Convert<std::decay_t<A>>::from(args.items[I])...};
    auto start = std::chrono::steady_clock::now();
    R result = function(std::get<I>(values)...);
    ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start)
             .count();
    std::string out;
    Convert<std::decay_t<R>>::dump(out, result);
    return out;
}

template <class R, class... A>
std::string call(R (*function)(A...), const Json& args, double& ms) {
    return call(function, args, std::index_sequence_for<A...>{}, ms);
}

inline std::string case_record(size_t index, const Json& args) {
    std::string head = "{\"case\":" + std::to_string(index) + ",";
    try {
        double ms = 0;
        std::string value = call(&TASKGEN_ENTRYPOINT, args, ms);
        char buffer[48];
        std::snprintf(buffer, sizeof buffer, "%.6f", ms);
        return head + "\"ok\":true,\"ms\":" + buffer + ",\"value\":" + value + "}";
    } catch (const std::exception& error) {
        std::string out = head + "\"ok\":false,\"error\":";
        dump_string(out, error.what());
        return out + "}";
    } catch (...) {
        return head + "\"ok\":false,\"error\":\"исключение не из std::exception\"}";
    }
}

inline void write_all(int fd, const std::string& data) {
    size_t done = 0;
    while (done < data.size()) {
        ssize_t n = ::write(fd, data.data() + done, data.size() - done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) std::_Exit(3);
        done += static_cast<size_t>(n);
    }
}

// Каждый кейс идёт в отдельном процессе: падение по сигналу или exit в решении губит
// только свой кейс, а глобальное состояние решения не переходит в следующий.
inline std::string run_isolated(size_t index, const Json& args) {
    int pipe_fds[2];
    if (::pipe(pipe_fds) != 0) throw std::runtime_error("pipe не создан");
    pid_t child = ::fork();
    if (child < 0) throw std::runtime_error("fork не удался");
    if (child == 0) {
        ::close(pipe_fds[0]);
        write_all(pipe_fds[1], case_record(index, args));
        std::_Exit(0);
    }
    ::close(pipe_fds[1]);
    std::string record;
    char buffer[65536];
    ssize_t n;
    while ((n = ::read(pipe_fds[0], buffer, sizeof buffer)) != 0) {
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) break;
        record.append(buffer, static_cast<size_t>(n));
    }
    ::close(pipe_fds[0]);
    int status = 0;
    while (::waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0 && !record.empty()) return record;
    std::string error;
    if (WIFSIGNALED(status))
        error = std::string("аварийное завершение: ") + ::strsignal(WTERMSIG(status));
    else
        error = "решение завершило процесс с кодом " + std::to_string(WEXITSTATUS(status));
    std::string out = "{\"case\":" + std::to_string(index) + ",\"ok\":false,\"error\":";
    dump_string(out, error);
    return out + "}";
}

}  // namespace taskgen_harness

int main() {
    using namespace taskgen_harness;
    std::string nonce, line;
    std::getline(std::cin, nonce);
    std::getline(std::cin, line);
    Json inputs = Parser(line).parse();

    // Канал протокола — копия исходного stdout. Сам stdout и stdin уводятся в /dev/null,
    // чтобы вывод и ввод решения не смешивались с протоколом.
    int channel = ::dup(1);
    int null = ::open("/dev/null", O_RDWR);
    ::dup2(null, 0);
    ::dup2(null, 1);

    for (size_t i = 0; i < inputs.items.size(); i++) {
        // Запись уходит сразу: если следующий кейс упрётся в лимит, готовые сохранятся.
        write_all(channel, nonce + " " + run_isolated(i, inputs.items[i]) + "\n");
    }
    return 0;
}
