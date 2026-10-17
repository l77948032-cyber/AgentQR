using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace ScanBridge;

public sealed record ScanResult(int Index, string Kind, string Destination,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? Payload);

public static partial class PayloadPolicy
{
    public static ScanResult Describe(string payload, int index, bool reveal = false)
    {
        var web = WebUri(payload);
        if (web is not null)
        {
            var host = web.HostNameType == UriHostNameType.IPv6
                ? $"[{web.IdnHost.Trim('[', ']')}]" : web.IdnHost;
            var port = web.IsDefaultPort ? "" : $":{web.Port}";
            return new(index, "web", $"{web.Scheme}://{host}{port}/[redacted]", reveal ? payload : null);
        }
        return new(index, SchemePattern().IsMatch(payload) ? "unsupported-scheme" : "text",
            "[redacted]", reveal ? payload : null);
    }

    public static Uri? WebUri(string payload)
    {
        if (payload.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) ||
            !Uri.TryCreate(payload, UriKind.Absolute, out var uri) ||
            !uri.IsWellFormedOriginalString() ||
            (uri.Scheme != Uri.UriSchemeHttps && uri.Scheme != Uri.UriSchemeHttp) ||
            string.IsNullOrEmpty(uri.Host) || !string.IsNullOrEmpty(uri.UserInfo))
            return null;
        return uri;
    }

    [GeneratedRegex("^[A-Za-z][A-Za-z0-9+.-]*:", RegexOptions.CultureInvariant)]
    private static partial Regex SchemePattern();
}
