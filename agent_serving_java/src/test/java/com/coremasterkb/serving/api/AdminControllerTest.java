package com.coremasterkb.serving.api;

import com.coremasterkb.serving.domainpack.DomainRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class AdminControllerTest {

    @Test
    void manualReloadIsRemovedButStatusRemains() throws Exception {
        DomainRegistry registry = mock(DomainRegistry.class);
        AdminController controller = new AdminController(registry);
        MockMvc mvc = MockMvcBuilders.standaloneSetup(controller).build();

        mvc.perform(post("/api/v1/admin/reload-config")).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/admin/config-status")).andExpect(status().isOk());
    }
}
