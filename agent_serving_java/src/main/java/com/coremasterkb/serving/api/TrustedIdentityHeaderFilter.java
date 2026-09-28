package com.coremasterkb.serving.api;

import com.coremasterkb.serving.config.ServingProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Trust boundary for gateway-derived identity headers.
 *
 * <p>Anonymous requests remain available where the existing controller permits them. As soon as
 * either user header is non-blank, the caller must prove it came through the trusted internal
 * channel. A username without a user id remains supported for old tokens; downstream attribution
 * keeps its existing username lookup fallback.</p>
 */
@Component
public class TrustedIdentityHeaderFilter extends OncePerRequestFilter {
    private final byte[] expectedSecret;

    public TrustedIdentityHeaderFilter(ServingProperties properties) {
        String configured = properties.internalAuth() == null
                ? "" : properties.internalAuth().secret();
        expectedSecret = configured == null
                ? new byte[0] : configured.trim().getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        if (!nonBlank(request.getHeader("X-KB-User"))
                && !nonBlank(request.getHeader("X-KB-User-Id"))) {
            filterChain.doFilter(request, response);
            return;
        }
        if (expectedSecret.length == 0) {
            reject(response, HttpServletResponse.SC_SERVICE_UNAVAILABLE,
                    "internal_auth_not_configured");
            return;
        }
        String supplied = request.getHeader("X-Internal-Auth");
        byte[] actual = supplied == null
                ? new byte[0] : supplied.getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(expectedSecret, actual)) {
            reject(response, HttpServletResponse.SC_UNAUTHORIZED, "unauthenticated");
            return;
        }
        filterChain.doFilter(request, response);
    }

    private static boolean nonBlank(String value) {
        return value != null && !value.isBlank();
    }

    private static void reject(HttpServletResponse response, int status, String code)
            throws IOException {
        response.setStatus(status);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write("{\"error\":\"" + code + "\"}");
    }
}